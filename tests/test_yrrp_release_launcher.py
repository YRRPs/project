from __future__ import annotations

import ast
import contextlib
import fcntl
import io
import json
import runpy
import shutil
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_release import launcher
from yrrp_release.launcher import (
    PROJECT_UPDATE_SCRIPT,
    REMOTE_LAUNCH_SCRIPT,
    REMOTE_LAUNCH_WORKER_SCRIPT,
    REMOTE_PREPARE_SCRIPT,
    ReleaseError,
    launch_release,
    parse_repository_arguments,
    prepare_release,
    remote_python_argv,
    run_remote,
    validate_repo_path,
    validate_sha,
    validate_sha256,
    verify_local_project,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


def load_embedded_functions(script: str, names: set[str], namespace: dict) -> dict:
    functions = [
        node
        for node in ast.parse(script).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "remote", "exec"),
        namespace,
    )
    return namespace


class InputValidationTest(unittest.TestCase):
    def test_accepts_lowercase_forty_character_sha(self) -> None:
        self.assertEqual(SHA_A, validate_sha(SHA_A, "project SHA"))

    def test_rejects_invalid_shas(self) -> None:
        for value in ("a" * 39, "A" * 40, "g" * 40, "a" * 41, ""):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "project SHA"):
                validate_sha(value, "project SHA")

    def test_accepts_lowercase_sha256(self) -> None:
        digest = "c" * 64
        self.assertEqual(digest, validate_sha256(digest, "manifest SHA-256"))

    def test_rejects_invalid_sha256(self) -> None:
        for value in ("c" * 63, "C" * 64, "g" * 64, "c" * 65, ""):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "manifest SHA-256"):
                validate_sha256(value, "manifest SHA-256")

    def test_accepts_safe_android_repo_paths(self) -> None:
        for value in (
            "art",
            "bionic",
            "libcore",
            "frameworks/base",
            "packages/apps/Settings",
            "vendor/yrrp-extra",
        ):
            with self.subTest(value=value):
                self.assertEqual(value, validate_repo_path(value))

    def test_rejects_unsafe_android_repo_paths(self) -> None:
        unsafe = (
            "",
            ".",
            "..",
            "/frameworks/base",
            "../frameworks/base",
            "frameworks/../base",
            "frameworks//base",
            "frameworks/base;touch-x",
            "frameworks/base $(id)",
            "frameworks/base\\other",
            "-frameworks/base",
        )
        for value in unsafe:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "repository path"):
                validate_repo_path(value)

    def test_repository_arguments_are_sorted_deterministically(self) -> None:
        parsed = parse_repository_arguments(
            [f"packages/apps/Settings={SHA_B}", f"frameworks/base={SHA_A}"]
        )
        self.assertEqual(
            {"frameworks/base": SHA_A, "packages/apps/Settings": SHA_B}, parsed
        )
        self.assertEqual(sorted(parsed), list(parsed))

    def test_repository_arguments_reject_duplicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate repository path"):
            parse_repository_arguments(
                [f"frameworks/base={SHA_A}", f"frameworks/base={SHA_B}"]
            )

    def test_repository_argument_requires_path_sha_separator(self) -> None:
        with self.assertRaisesRegex(ValueError, "PATH=SHA"):
            parse_repository_arguments(["frameworks/base"])


class LocalCheckoutTest(unittest.TestCase):
    @patch("yrrp_release.launcher.subprocess.run")
    def test_rejects_dirty_local_checkout(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout=" M file\n", stderr="")
        with self.assertRaisesRegex(ValueError, "local project checkout is dirty"):
            verify_local_project(SHA_A, ROOT)
        self.assertEqual(["git", "status", "--porcelain"], run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs["shell"])

    @patch("yrrp_release.launcher.subprocess.run")
    def test_rejects_wrong_local_head(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=SHA_B + "\n", stderr=""),
        ]
        with self.assertRaisesRegex(ValueError, "does not match requested"):
            verify_local_project(SHA_A, ROOT)

    @patch("yrrp_release.launcher.subprocess.run")
    def test_accepts_clean_exact_local_head(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=SHA_A + "\n", stderr=""),
        ]
        verify_local_project(SHA_A, ROOT)
        for call in run.call_args_list:
            self.assertIsInstance(call.args[0], list)
            self.assertFalse(call.kwargs["shell"])


class RemoteInvocationTest(unittest.TestCase):
    def test_remote_python_survives_ssh_argument_join(self) -> None:
        script = "print(\"it's quoted; $HOME `true`\")"
        argv = remote_python_argv(script)
        self.assertEqual(["python3", "-c", script], shlex.split(" ".join(argv[2:])))

    def test_remote_call_uses_fixed_argv_shell_false_and_json_stdin(self) -> None:
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}\n', stderr="")

        result = run_remote("print('fixed')", {"project_sha": SHA_A}, runner=fake_run)
        self.assertEqual({"ok": True}, result)
        argv, kwargs = calls[0]
        self.assertIsInstance(argv, list)
        self.assertFalse(kwargs["shell"])
        self.assertEqual({"project_sha": SHA_A}, json.loads(kwargs["input"]))

    def test_remote_failure_reports_concise_stderr_and_stdout_tails(self) -> None:
        long_stderr = "\n".join(f"err-{index}" for index in range(60))
        long_stdout = "\n".join(f"out-{index}" for index in range(60))

        def fail(argv, **kwargs):
            raise subprocess.CalledProcessError(
                17, argv, output=long_stdout, stderr=long_stderr
            )

        with self.assertRaises(ReleaseError) as caught:
            run_remote("pass", {}, runner=fail)
        message = str(caught.exception)
        self.assertIn("err-59", message)
        self.assertIn("out-59", message)
        self.assertNotIn("err-0", message)
        self.assertNotIn("out-0", message)


class PublicLauncherTest(unittest.TestCase):
    @patch("yrrp_release.launcher.run_remote")
    @patch("yrrp_release.launcher.verify_local_project")
    def test_prepare_validates_local_source_and_never_launches_build(
        self, verify, remote
    ) -> None:
        remote.return_value = {"project_sha": SHA_A, "repositories": {}}
        result = prepare_release(SHA_A, {})
        verify.assert_called_once_with(SHA_A)
        remote.assert_called_once_with(REMOTE_PREPARE_SCRIPT, {
            "project_sha": SHA_A,
            "repositories": {},
        })
        self.assertEqual(SHA_A, result["project_sha"])

    def test_launch_requires_exact_literal_approval(self) -> None:
        for approval in ("build and release", "Build and release ", "Build & release", ""):
            with self.subTest(approval=approval), self.assertRaisesRegex(
                ValueError, "exact approval"
            ):
                launch_release(approval, SHA_A, {}, "c" * 64)

    @patch("yrrp_release.launcher.run_remote")
    @patch("yrrp_release.launcher.verify_local_project")
    def test_launch_sends_only_requested_source_and_returns_evidence(
        self, verify, remote
    ) -> None:
        evidence = {
            "log": "/opt/android/out/signed/yrrp-ota-build.log",
            "source": {"project_sha": SHA_A, "repositories": {"frameworks/base": SHA_B}},
        }
        remote.return_value = evidence
        result = launch_release(
            "Build and release", SHA_A, {"frameworks/base": SHA_B}, "c" * 64
        )
        verify.assert_called_once_with(SHA_A)
        remote.assert_called_once_with(
            REMOTE_LAUNCH_SCRIPT,
            {
                "project_sha": SHA_A,
                "repositories": {"frameworks/base": SHA_B},
                "manifest_sha256": "c" * 64,
            },
        )
        self.assertEqual(evidence, result)


class CliContractTest(unittest.TestCase):
    def test_launch_parser_requires_manifest_sha256(self) -> None:
        namespace = runpy.run_path(str(SCRIPTS / "yrrp-release.py"))
        parse_args = namespace["parse_args"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args([
                "launch",
                "--approval", "Build and release",
                "--project-sha", SHA_A,
            ])
        arguments = parse_args([
            "launch",
            "--approval", "Build and release",
            "--project-sha", SHA_A,
            "--manifest-sha256", "c" * 64,
            "--repo", f"frameworks/base={SHA_B}",
        ])
        self.assertEqual("c" * 64, arguments.manifest_sha256)


class RemotePrepareContractTest(unittest.TestCase):
    def test_prepare_uses_nonblocking_shared_release_lock(self) -> None:
        self.assertIn("/home/android/.yrrp-build-launch.lock", REMOTE_PREPARE_SCRIPT)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", REMOTE_PREPARE_SCRIPT)
        self.assertIn("another release operation holds", REMOTE_PREPARE_SCRIPT)

    def test_prepare_process_detection_includes_signer_entry(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_PREPARE_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "build_process_active"
        )
        program = next(
            node
            for node in ast.parse(REMOTE_PREPARE_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "program_name"
        )
        namespace = {"shlex": shlex}
        exec(compile(ast.Module(body=[program, function], type_ignores=[]), "remote", "exec"), namespace)
        active = namespace["build_process_active"]
        self.assertTrue(active("bash /opt/yrrp/project/scripts/sign-lineage-build.sh\n"))
        self.assertTrue(active("ninja -f out/build.ninja\n"))
        self.assertFalse(active("grep sign-lineage-build.sh\n"))

    def test_prepare_checks_process_screen_signing_and_network_preflight(self) -> None:
        for text in (
            '"ps", "-eo", "args"',
            '"screen", "-list"',
            "yrrp-ota-build",
            '"sha256sum", "--check", "--quiet", "MANIFEST.sha256"',
            "testkey.pk8",
            "testkey.x509.pem",
            '"docker", "network", "inspect", "proxy-net"',
        ):
            with self.subTest(text=text):
                self.assertIn(text, REMOTE_PREPARE_SCRIPT)

    def test_remote_prepare_embeds_project_update_program(self) -> None:
        module = ast.parse(REMOTE_PREPARE_SCRIPT)
        assigned = {
            target.id
            for node in module.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertIn("PROJECT_UPDATE_SCRIPT", assigned)

    def test_project_sync_uses_running_builder_image_uid_mount_and_no_pull(self) -> None:
        for text in (
            '"docker", "inspect"',
            "lineageos-builder",
            ".State.Running",
            ".Image",
            '"docker", "run", "--rm"',
            '"--user", "950:950"',
            "/mnt/fast/docker/android/project",
            '":/project:rw"',
            '"/opt/yrrp/project"',
        ):
            with self.subTest(text=text):
                self.assertIn(text, REMOTE_PREPARE_SCRIPT)
        self.assertNotIn("docker pull", REMOTE_PREPARE_SCRIPT)
        self.assertNotIn('"pull"', REMOTE_PREPARE_SCRIPT)

    def test_project_update_refuses_dirty_fetches_public_sha_and_detaches_exact(self) -> None:
        self.assertIn('"git", "status", "--porcelain"', PROJECT_UPDATE_SCRIPT)
        self.assertIn("https://github.com/YRRPs/lineageos-salami-custom.git", PROJECT_UPDATE_SCRIPT)
        self.assertIn('"git", "fetch"', PROJECT_UPDATE_SCRIPT)
        self.assertIn('"git", "checkout", "--detach"', PROJECT_UPDATE_SCRIPT)
        self.assertIn('"git", "rev-parse", "HEAD"', PROJECT_UPDATE_SCRIPT)

    def test_prepare_syncs_and_detaches_each_requested_repo_at_exact_sha(self) -> None:
        calls = []
        namespace = load_embedded_functions(
            REMOTE_PREPARE_SCRIPT,
            {"synchronize_repositories"},
            {
                "REPO": "repo",
                "ANDROID": Path("/opt/android"),
                "run": lambda *args, **kwargs: calls.append((args, kwargs)),
            },
        )

        namespace["synchronize_repositories"]({"art": SHA_A})

        self.assertEqual(
            [
                (("repo", "sync", "art"), {"cwd": Path("/opt/android")}),
                (("git", "cat-file", "-e", f"{SHA_A}^{{commit}}"), {"cwd": Path("/opt/android/art")}),
                (("git", "checkout", "--detach", SHA_A), {"cwd": Path("/opt/android/art")}),
            ],
            calls,
        )
        self.assertNotIn('REPO, "sync"]', REMOTE_PREPARE_SCRIPT)
        self.assertNotIn("--force-sync", REMOTE_PREPARE_SCRIPT)
        self.assertNotIn("git reset", REMOTE_PREPARE_SCRIPT)
        self.assertNotIn("git clean", REMOTE_PREPARE_SCRIPT)

    def test_prepare_checks_full_cleanliness_before_any_source_mutation(self) -> None:
        events = []

        def reject_dirty():
            events.append("full-clean-check")
            raise SystemExit("dirty unlisted repository")

        namespace = load_embedded_functions(
            REMOTE_PREPARE_SCRIPT,
            {"prepare_source"},
            {
                "reject_active_release": lambda: events.append("active-check"),
                "validate_signing_and_network": lambda: events.append("preflight"),
                "validate_all_repositories_clean": reject_dirty,
                "synchronize_project": lambda *_: events.append("project-sync"),
                "synchronize_repositories": lambda *_: events.append("repo-sync"),
                "capture_source": lambda *_args, **_kwargs: events.append("capture"),
            },
        )
        with self.assertRaisesRegex(SystemExit, "dirty unlisted repository"):
            namespace["prepare_source"](
                {"project_sha": SHA_A, "repositories": {"frameworks/base": SHA_B}}
            )
        self.assertEqual(["active-check", "preflight", "full-clean-check"], events)

    def test_dirty_manifest_checkout_fails_with_path_before_repo_forall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            android = Path(directory)
            manifests = android / ".repo/manifests"
            manifests.mkdir(parents=True)
            repo_calls = []
            namespace = load_embedded_functions(
                REMOTE_PREPARE_SCRIPT,
                {"validate_all_repositories_clean"},
                {
                    "ANDROID": android,
                    "REPO": "repo",
                    "output": lambda *_args, **_kwargs: " M default.xml",
                    "subprocess": types.SimpleNamespace(
                        run=lambda *args, **kwargs: repo_calls.append((args, kwargs))
                    ),
                    "sys": sys,
                },
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(
                SystemExit, "manifest checkout is dirty"
            ):
                namespace["validate_all_repositories_clean"]()
            self.assertIn(str(manifests), stderr.getvalue())
            self.assertIn("M default.xml", stderr.getvalue())
            self.assertEqual([], repo_calls)

    def test_nonempty_local_manifests_are_forbidden_with_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            android = Path(directory)
            (android / ".repo/manifests").mkdir(parents=True)
            local = android / ".repo/local_manifests"
            local.mkdir()
            override = local / "private.xml"
            override.write_text("<manifest/>\n")
            repo_calls = []
            namespace = load_embedded_functions(
                REMOTE_LAUNCH_WORKER_SCRIPT,
                {"validate_all_repositories_clean"},
                {
                    "ANDROID": android,
                    "REPO": "repo",
                    "output": lambda *_args, **_kwargs: "",
                    "subprocess": types.SimpleNamespace(
                        run=lambda *args, **kwargs: repo_calls.append((args, kwargs))
                    ),
                    "sys": sys,
                },
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(
                SystemExit, "local_manifests is forbidden"
            ):
                namespace["validate_all_repositories_clean"]()
            self.assertIn(str(override), stderr.getvalue())
            self.assertEqual([], repo_calls)

    def test_unlisted_dirty_repo_fails_prepare_and_launch_cleanliness(self) -> None:
        for label, script in (
            ("prepare", REMOTE_PREPARE_SCRIPT),
            ("launch-worker", REMOTE_LAUNCH_WORKER_SCRIPT),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / ".repo/manifests").mkdir(parents=True)
                fake_repo = Path(directory) / "repo"
                fake_repo.write_text(
                    "#!/bin/sh\n"
                    "[ \"$1 $2 $3\" = \"forall -e -c\" ] || exit 91\n"
                    "printf 'vendor/unlisted\\n M changed.txt\\n' >&2\n"
                    "exit 7\n"
                )
                fake_repo.chmod(0o755)
                namespace = load_embedded_functions(
                    script,
                    {"validate_all_repositories_clean"},
                    {
                        "subprocess": subprocess,
                        "sys": sys,
                        "REPO": str(fake_repo),
                        "ANDROID": Path(directory),
                        "output": lambda *_args, **_kwargs: "",
                    },
                )
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaisesRegex(
                    SystemExit, "dirty repositories"
                ):
                    namespace["validate_all_repositories_clean"]()
                self.assertIn("vendor/unlisted", stderr.getvalue())
                self.assertIn("M changed.txt", stderr.getvalue())
        self.assertIn("validate_all_repositories_clean()", REMOTE_LAUNCH_WORKER_SCRIPT)

    def test_prepare_captures_exact_clean_source_and_revision_locked_manifest(self) -> None:
        for text in (
            'REPO, "manifest", "-r"',
            "manifest_sha256",
            '"project_sha"',
            '"repositories"',
            '"branches"',
            '"clean_repositories"',
        ):
            with self.subTest(text=text):
                self.assertIn(text, REMOTE_PREPARE_SCRIPT)
        self.assertIn("repository SHA", REMOTE_PREPARE_SCRIPT)
        self.assertIn("repository checkout is dirty", REMOTE_PREPARE_SCRIPT)

    def test_prepare_does_not_start_screen_or_signer(self) -> None:
        self.assertNotIn('"-dmS"', REMOTE_PREPARE_SCRIPT)
        self.assertNotIn("sign-lineage-build.sh", REMOTE_PREPARE_SCRIPT)


class RemoteLaunchContractTest(unittest.TestCase):
    WORKER_FUNCTIONS = {
        "acquire_nonblocking_lock",
        "failure_reason",
        "launch_worker",
        "write_launch_ack",
        "write_launch_status",
    }

    def worker_namespace(self, directory: str, capture_source) -> dict:
        root = Path(directory)
        namespace = {
            "fcntl": fcntl,
            "json": json,
            "os": __import__("os"),
            "subprocess": subprocess,
            "Path": Path,
            "LOCK_PATH": root / "build.lock",
            "STATUS_PATH": root / "status",
            "capture_source": capture_source,
            "reject_active_release": lambda **_: None,
            "validate_signing_and_network": lambda: None,
            "validate_signer_entry": lambda: None,
        }
        return load_embedded_functions(
            REMOTE_LAUNCH_WORKER_SCRIPT, self.WORKER_FUNCTIONS, namespace
        )

    def test_remote_launch_embeds_detached_worker_program(self) -> None:
        module = ast.parse(REMOTE_LAUNCH_SCRIPT)
        assigned = {
            target.id
            for node in module.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertIn("REMOTE_LAUNCH_WORKER_SCRIPT", assigned)

    def test_parent_has_no_release_lock_and_initializes_screen_runtime(self) -> None:
        top_level = REMOTE_LAUNCH_SCRIPT.split("payload = json.load(sys.stdin)", 1)[1]
        self.assertNotIn("acquire_nonblocking_lock", top_level)
        self.assertIn(
            '"sudo", "install", "-d", "-m", "0777", "-o", "root", "-g", "utmp", "/run/screen"',
            REMOTE_LAUNCH_SCRIPT,
        )
        self.assertIn('"screen", "-L", "-Logfile"', REMOTE_LAUNCH_SCRIPT)
        self.assertIn('"-dmS", "yrrp-ota-build"', REMOTE_LAUNCH_SCRIPT)
        self.assertIn("ack_directory.mkdir(mode=0o700)", REMOTE_LAUNCH_SCRIPT)
        self.assertNotIn("mkdir", REMOTE_LAUNCH_WORKER_SCRIPT)

    def test_worker_lock_contention_acknowledges_failure_without_signer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = {"manifest_sha256": "c" * 64}
            namespace = self.worker_namespace(directory, lambda *_: source)
            lock_path = namespace["LOCK_PATH"]
            lock_path.touch()
            signer_called = False
            namespace["STATUS_PATH"].write_text("building-target-files\n")
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)

            def signer_spawn(*_args, **_kwargs):
                nonlocal signer_called
                signer_called = True

            with lock_path.open("r+") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(SystemExit, "another release operation holds"):
                    namespace["launch_worker"](
                        {"project_sha": SHA_A, "repositories": {}, "manifest_sha256": "c" * 64},
                        ack_directory,
                        signer_spawn,
                    )
            self.assertFalse(signer_called)
            ack = json.loads((ack_directory / "ack.json").read_text())
            self.assertFalse(ack["ok"])
            self.assertEqual(
                "building-target-files", namespace["STATUS_PATH"].read_text().strip()
            )

    def test_worker_holds_lock_across_final_recheck_and_signer_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = {"manifest_sha256": "c" * 64}
            namespace = self.worker_namespace(directory, lambda *_: source)
            second_client_blocked = False

            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)
            spawn_observed_before_success = False

            class FakeSigner:
                def wait(self):
                    nonlocal second_client_blocked
                    with namespace["LOCK_PATH"].open("r+") as contender:
                        try:
                            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            second_client_blocked = True
                    return 0

            def signer_spawn(argv, *, env, pass_fds):
                nonlocal spawn_observed_before_success
                self.assertEqual(
                    ["/opt/yrrp/project/scripts/sign-lineage-build.sh"], argv
                )
                self.assertEqual("1", env["YRRP_BUILD_LOCK_HELD"])
                self.assertEqual(1, len(pass_fds))
                self.assertEqual(str(pass_fds[0]), env["YRRP_BUILD_LOCK_FD"])
                self.assertFalse((ack_directory / "ack.json").exists())
                self.assertEqual(
                    "launch-starting-signer",
                    namespace["STATUS_PATH"].read_text().strip(),
                )
                namespace["STATUS_PATH"].write_text("building-target-files\n")
                spawn_observed_before_success = True
                return FakeSigner()

            result = namespace["launch_worker"](
                {"project_sha": SHA_A, "repositories": {}, "manifest_sha256": "c" * 64},
                ack_directory,
                signer_spawn,
            )
            self.assertEqual(0, result)
            self.assertTrue(spawn_observed_before_success)
            self.assertTrue(second_client_blocked)
            self.assertTrue(json.loads((ack_directory / "ack.json").read_text())["ok"])
            self.assertEqual(
                "building-target-files", namespace["STATUS_PATH"].read_text().strip()
            )

    def test_manifest_drift_replaces_stale_complete_with_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status").write_text("complete\n")
            status_seen_during_recheck = ""
            signer_called = False

            def capture(*_):
                nonlocal status_seen_during_recheck
                status_seen_during_recheck = (root / "status").read_text().strip()
                return {"manifest_sha256": "d" * 64}

            def signer_spawn(*_args, **_kwargs):
                nonlocal signer_called
                signer_called = True

            ack_directory = root / "ack"
            ack_directory.mkdir(mode=0o700)
            namespace = self.worker_namespace(directory, capture)
            with self.assertRaisesRegex(SystemExit, "manifest"):
                namespace["launch_worker"](
                    {"project_sha": SHA_A, "repositories": {}, "manifest_sha256": "c" * 64},
                    ack_directory,
                    signer_spawn,
                )
            self.assertEqual("launch-validating", status_seen_during_recheck)
            self.assertTrue((root / "status").read_text().startswith("launch-failed:"))
            self.assertFalse(json.loads((ack_directory / "ack.json").read_text())["ok"])
            self.assertFalse(signer_called)

    def test_signer_spawn_failure_never_publishes_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ack_directory = root / "ack"
            ack_directory.mkdir(mode=0o700)
            namespace = self.worker_namespace(
                directory, lambda *_: {"manifest_sha256": "c" * 64}
            )

            def fail_spawn(*_args, **_kwargs):
                raise OSError("synthetic spawn failure")

            with self.assertRaisesRegex(OSError, "synthetic spawn failure"):
                namespace["launch_worker"](
                    {"project_sha": SHA_A, "repositories": {}, "manifest_sha256": "c" * 64},
                    ack_directory,
                    fail_spawn,
                )
            acknowledgment = json.loads((ack_directory / "ack.json").read_text())
            self.assertFalse(acknowledgment["ok"])
            self.assertIn("synthetic spawn failure", acknowledgment["error"])

    def test_command_failures_propagate_bounded_output_tails(self) -> None:
        for label in ("repo", "docker", "checksum"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ack_directory = root / "ack"
                ack_directory.mkdir(mode=0o700)
                namespace = self.worker_namespace(
                    directory, lambda *_: {"manifest_sha256": "c" * 64}
                )
                stderr = "secret-first-line\n" + "\n".join(
                    f"{label}-stderr-{index}" for index in range(45)
                )
                stdout = "\n".join(
                    f"{label}-stdout-{index}" for index in range(45)
                )

                def fail_check():
                    raise subprocess.CalledProcessError(
                        23, [label], output=stdout, stderr=stderr
                    )

                namespace["validate_signing_and_network"] = fail_check
                expected_log = f"/opt/android/out/signed/{label}-screen.log"
                with self.assertRaises(subprocess.CalledProcessError):
                    namespace["launch_worker"](
                        {
                            "project_sha": SHA_A,
                            "repositories": {},
                            "manifest_sha256": "c" * 64,
                            "screen_log": expected_log,
                        },
                        ack_directory,
                        lambda *_args, **_kwargs: None,
                    )
                acknowledgment = json.loads((ack_directory / "ack.json").read_text())
                reason = acknowledgment["error"]
                self.assertEqual(expected_log, acknowledgment["log"])
                self.assertIn(f"{label}-stderr-44", reason)
                self.assertIn(f"{label}-stdout-44", reason)
                self.assertNotIn("secret-first-line", reason)

    def test_parent_failure_names_exact_screen_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)
            (ack_directory / "ack.json").write_text(
                json.dumps({"ok": False, "error": "docker-stderr-tail"})
            )
            log = Path("/opt/android/out/signed/yrrp-ota-build-exact.log")
            namespace = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {"json": json, "shutil": shutil, "time": __import__("time")},
            )
            with self.assertRaisesRegex(SystemExit, str(log)) as caught:
                namespace["wait_for_launch_ack"](
                    ack_directory, log_path=log, attempts=1
                )
            self.assertIn("docker-stderr-tail", str(caught.exception))
            self.assertFalse(ack_directory.exists())

    def test_parent_detects_worker_exit_without_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)

            def no_screen(*_args, **_kwargs):
                return subprocess.CompletedProcess([], 0, stdout="No Sockets found.\n", stderr="")

            namespace = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {
                    "json": json,
                    "shutil": shutil,
                    "time": __import__("time"),
                    "run": no_screen,
                },
            )
            with self.assertRaisesRegex(SystemExit, "worker exited"):
                namespace["wait_for_launch_ack"](ack_directory, attempts=1)
            self.assertFalse(ack_directory.exists())

    def test_parent_rechecks_atomic_ack_after_screen_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)
            expected = {"ok": True, "source": {"manifest_sha256": "c" * 64}}

            def screen_exits_after_ack(*_args, **_kwargs):
                (ack_directory / "ack.json").write_text(json.dumps(expected))
                return subprocess.CompletedProcess([], 0, stdout="No Sockets found.\n", stderr="")

            namespace = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {
                    "json": json,
                    "shutil": shutil,
                    "time": __import__("time"),
                    "run": screen_exits_after_ack,
                },
            )
            self.assertEqual(
                expected, namespace["wait_for_launch_ack"](ack_directory, attempts=1)
            )
            self.assertFalse(ack_directory.exists())

    def test_parent_rejects_worker_failure_ack_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)
            (ack_directory / "ack.json").write_text(
                json.dumps({"ok": False, "error": "lock busy"})
            )
            screen_calls = []
            namespace = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {
                    "json": json,
                    "shutil": shutil,
                    "time": __import__("time"),
                    "run": lambda *args, **kwargs: screen_calls.append((args, kwargs)),
                },
            )
            with self.assertRaisesRegex(SystemExit, "lock busy"):
                namespace["wait_for_launch_ack"](ack_directory, attempts=1)
            self.assertFalse(ack_directory.exists())
            self.assertEqual([], screen_calls)

    def test_parent_success_removes_ack_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)
            expected = {"ok": True, "source": {"manifest_sha256": "c" * 64}}
            (ack_directory / "ack.json").write_text(json.dumps(expected))
            namespace = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {"json": json, "shutil": shutil, "time": __import__("time")},
            )
            self.assertEqual(
                expected, namespace["wait_for_launch_ack"](ack_directory, attempts=1)
            )
            self.assertFalse(ack_directory.exists())

    def test_timeout_removes_directory_and_late_worker_cannot_recreate_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ack_directory = Path(directory) / "ack"
            ack_directory.mkdir(mode=0o700)

            def active_screen(*_args, **_kwargs):
                return subprocess.CompletedProcess(
                    [], 0, stdout="123.yrrp-ota-build\n", stderr=""
                )

            parent = load_embedded_functions(
                REMOTE_LAUNCH_SCRIPT,
                {"wait_for_launch_ack"},
                {
                    "json": json,
                    "shutil": shutil,
                    "time": __import__("time"),
                    "run": active_screen,
                },
            )
            with self.assertRaisesRegex(SystemExit, "timed out"):
                parent["wait_for_launch_ack"](ack_directory, attempts=1)
            self.assertFalse(ack_directory.exists())

            worker = load_embedded_functions(
                REMOTE_LAUNCH_WORKER_SCRIPT,
                {"write_launch_ack"},
                {"json": json},
            )
            self.assertFalse(
                worker["write_launch_ack"](ack_directory, {"ok": True})
            )
            self.assertFalse(ack_directory.exists())

    def test_launch_returns_log_and_locked_source_evidence(self) -> None:
        self.assertIn('"log": str(log)', REMOTE_LAUNCH_SCRIPT)
        self.assertIn('"source": acknowledgment["source"]', REMOTE_LAUNCH_SCRIPT)


class RemoteJsonChannelTest(unittest.TestCase):
    def test_top_level_remote_scripts_emit_one_json_document(self) -> None:
        for label, script in (
            ("prepare", REMOTE_PREPARE_SCRIPT),
            ("launch", REMOTE_LAUNCH_SCRIPT),
        ):
            prints = [
                node
                for node in ast.walk(ast.parse(script))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ]
            with self.subTest(label=label):
                self.assertEqual(1, len(prints))
                self.assertEqual("json.dumps", ast.unparse(prints[0].args[0].func))


if __name__ == "__main__":
    unittest.main()

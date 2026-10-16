from __future__ import annotations

import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_release.checks import (
    CHECK_TARGET_SCRIPT,
    ISOLATED_OVERLAY_PREP_SCRIPT,
    ISOLATED_WORKSPACE_SETUP_SCRIPT,
    REMOTE_CHECK_INNER_SCRIPT,
    REMOTE_CHECK_SCRIPT,
    match_check_to_source,
    parse_atest_counts,
    parse_atest_result,
    start_check,
    validate_check_result,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST = "c" * 64
RUN_ID = "check-20261015-123456-a1b2c3d4"
SUCCESS = """
Summary
-------
ThemePickerRoboTests: Passed: 12, Failed: 0, Ignored: 0
12 Tests Passed
"""
ZERO = """
Summary
-------
ThemePickerRoboTests: Passed: 0, Failed: 0, Ignored: 0
0 Tests Passed
"""
COMPILE_ONLY = "Build completed successfully\n"
FAILED_MODULES = """
ThemePickerUnitTests: Passed: 10, Failed: 100
ThemePickerResourceTests: Passed: 6, Failed: 99
"""
FAILED_LINES = """
com.example.FirstTest testOne PASSED
com.example.SecondTest testTwo FAILED
com.example.ThirdTest testThree FAILED
"""


def valid_result() -> dict:
    return {
        "schema_version": 2,
        "run_id": RUN_ID,
        "workspace": "release",
        "device": "salami",
        "source": {
            "project_sha": SHA_A,
            "repositories": {"packages/apps/ThemePicker": SHA_B},
            "manifest_sha256": DIGEST,
        },
        "targets": [
            {
                "ids": ["m:ThemePicker"],
                "executor": "m",
                "argv": ["m", "ThemePicker"],
                "result": "passed",
                "exit_code": 0,
                "started_at": "2026-10-15T12:35:00Z",
                "completed_at": "2026-10-15T12:36:00Z",
            },
            {
                "ids": ["atest:ThemePickerRoboTests"],
                "executor": "atest",
                "argv": ["atest", "ThemePickerRoboTests"],
                "result": "passed",
                "exit_code": 0,
                "executed_tests": 12,
                "passed_tests": 12,
                "failed_tests": 0,
                "ignored_tests": 0,
                "started_at": "2026-10-15T12:36:00Z",
                "completed_at": "2026-10-15T12:37:00Z",
            },
        ],
        "started_at": "2026-10-15T12:34:56Z",
        "completed_at": "2026-10-15T12:37:01Z",
        "log": f"/opt/android/out/yrrp-checks/{RUN_ID}/job.log",
        "result": "passed",
        "exit_code": 0,
        "failure_reason": None,
    }


class AtestResultTest(unittest.TestCase):
    def test_requires_positive_execution_count(self) -> None:
        self.assertEqual(12, parse_atest_result(SUCCESS))
        for output in (ZERO, COMPILE_ONLY, ""):
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_atest_result(output)

    def test_aggregates_module_summaries_and_records_outcomes(self) -> None:
        self.assertEqual(
            {"passed": 16, "failed": 199, "ignored": 0, "executed": 215},
            parse_atest_counts(FAILED_MODULES),
        )

    def test_falls_back_to_passed_and_failed_result_lines(self) -> None:
        self.assertEqual(
            {"passed": 1, "failed": 2, "ignored": 0, "executed": 3},
            parse_atest_counts(FAILED_LINES),
        )

    def test_counts_failed_and_ignored_tests_as_execution(self) -> None:
        output = "Suite: Passed: 2, Failed: 1, Ignored: 3\n"
        self.assertEqual(6, parse_atest_result(output))


class CheckResultValidationTest(unittest.TestCase):
    def test_accepts_strict_result_and_returns_copy(self) -> None:
        source = valid_result()
        validated = validate_check_result(source)
        self.assertEqual(source, validated)
        self.assertIsNot(source, validated)
        self.assertIsNot(source["targets"], validated["targets"])

    def test_accepts_legacy_schema_one_counts(self) -> None:
        document = valid_result()
        document["schema_version"] = 1
        for key in ("passed_tests", "failed_tests", "ignored_tests"):
            document["targets"][1].pop(key)
        self.assertEqual(12, validate_check_result(document)["targets"][1]["executed_tests"])

    def test_rejects_unknown_or_missing_top_level_keys(self) -> None:
        for mutate in (
            lambda value: value.update({"unexpected": True}),
            lambda value: value.pop("device"),
        ):
            document = valid_result()
            mutate(document)
            with self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)

    def test_rejects_wrong_argv_duplicate_ids_and_zero_tests(self) -> None:
        mutations = []

        wrong_argv = valid_result()
        wrong_argv["targets"][0]["argv"] = ["sh", "-c", "id"]
        mutations.append(wrong_argv)

        duplicate = valid_result()
        duplicate["targets"][1]["ids"] = ["m:ThemePicker"]
        mutations.append(duplicate)

        zero = valid_result()
        zero["targets"][1]["executed_tests"] = 0
        mutations.append(zero)

        inconsistent_counts = valid_result()
        inconsistent_counts["targets"][1]["failed_tests"] = 1
        mutations.append(inconsistent_counts)

        for document in mutations:
            with self.subTest(document=document), self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)

    def test_accepts_failure_before_any_target_starts(self) -> None:
        document = valid_result()
        document["targets"] = []
        document["result"] = "failed"
        document["exit_code"] = 1
        document["failure_reason"] = "source synchronization failed"
        self.assertEqual([], validate_check_result(document)["targets"])

    def test_accepts_failed_atest_before_test_execution(self) -> None:
        document = valid_result()
        document["result"] = "failed"
        document["exit_code"] = 1
        document["failure_reason"] = "atest compilation failed"
        document["targets"][1]["result"] = "failed"
        document["targets"][1]["exit_code"] = 1
        document["targets"][1]["executed_tests"] = 0
        document["targets"][1]["passed_tests"] = 0
        self.assertEqual("failed", validate_check_result(document)["result"])

    def test_rejects_bad_timestamps_exit_codes_and_log_paths(self) -> None:
        documents = []
        reversed_time = valid_result()
        reversed_time["completed_at"] = "2026-10-15T12:00:00Z"
        documents.append(reversed_time)
        nonzero_pass = valid_result()
        nonzero_pass["targets"][0]["exit_code"] = 1
        documents.append(nonzero_pass)
        bad_log = valid_result()
        bad_log["log"] = "/tmp/check.log"
        documents.append(bad_log)
        for document in documents:
            with self.subTest(document=document), self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)


class SourceMatchTest(unittest.TestCase):
    def test_matches_release_check_to_exact_source_and_targets(self) -> None:
        checked = match_check_to_source(
            valid_result(),
            {
                "project_sha": SHA_A,
                "repositories": {"packages/apps/ThemePicker": SHA_B},
                "manifest_sha256": DIGEST,
            },
            ("m:ThemePicker",),
        )
        self.assertEqual(RUN_ID, checked["run_id"])

    def test_rejects_empty_or_missing_required_targets(self) -> None:
        with self.assertRaises(ValueError):
            match_check_to_source(valid_result(), valid_result()["source"], ())
        with self.assertRaises(ValueError):
            match_check_to_source(
                valid_result(), valid_result()["source"], ("m:SystemUI",)
            )

    def test_rejects_non_gating_robo_target_as_release_requirement(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-gating"):
            match_check_to_source(
                valid_result(),
                valid_result()["source"],
                ("atest:ThemePickerRoboTests",),
            )

    def test_rejects_check_for_different_required_device(self) -> None:
        with self.assertRaisesRegex(ValueError, "device"):
            match_check_to_source(
                valid_result(),
                valid_result()["source"],
                ("m:ThemePicker",),
                required_device="xueying",
            )

    def test_rejects_isolated_failed_or_stale_evidence(self) -> None:
        documents = []
        isolated = valid_result()
        isolated["workspace"] = "isolated"
        documents.append(isolated)
        failed = valid_result()
        failed["result"] = "failed"
        failed["exit_code"] = 1
        failed["failure_reason"] = "compile failed"
        failed["targets"][0]["result"] = "failed"
        failed["targets"][0]["exit_code"] = 1
        documents.append(failed)
        for document in documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                match_check_to_source(
                    document,
                    valid_result()["source"],
                    ("m:ThemePicker",),
                )

        for field, changed in (
            ("project_sha", "d" * 40),
            ("manifest_sha256", "e" * 64),
            ("repositories", {"packages/apps/ThemePicker": "f" * 40}),
        ):
            prepared = copy.deepcopy(valid_result()["source"])
            prepared[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                match_check_to_source(valid_result(), prepared, ("m:ThemePicker",))


class CheckLaunchTest(unittest.TestCase):
    @patch("yrrp_release.checks.run_remote")
    @patch("yrrp_release.checks.verify_local_project")
    def test_start_check_sends_only_validated_json(self, verify, remote) -> None:
        remote.return_value = {
            "run_id": RUN_ID,
            "workspace": "isolated",
            "status": f"/opt/android/out/yrrp-checks/{RUN_ID}/status.json",
            "log": f"/opt/android/out/yrrp-checks/{RUN_ID}/job.log",
        }
        result = start_check(
            workspace="isolated",
            project_sha=SHA_A,
            repositories={"packages/apps/ThemePicker": SHA_B},
            device="salami",
            target_ids=["m:ThemePicker", "atest:ThemePickerRoboTests"],
        )
        verify.assert_called_once_with(SHA_A)
        self.assertEqual(
            {
                "workspace": "isolated",
                "project_sha": SHA_A,
                "repositories": {"packages/apps/ThemePicker": SHA_B},
                "device": "salami",
                "targets": ["m:ThemePicker", "atest:ThemePickerRoboTests"],
            },
            remote.call_args.args[1],
        )
        self.assertEqual(RUN_ID, result["run_id"])

    @patch("yrrp_release.checks.run_remote")
    @patch("yrrp_release.checks.verify_local_project")
    def test_invalid_request_never_contacts_builder(self, verify, remote) -> None:
        cases = (
            {"workspace": "other", "device": "salami", "targets": ["m:ThemePicker"]},
            {"workspace": "isolated", "device": "other", "targets": ["m:ThemePicker"]},
            {"workspace": "isolated", "device": "salami", "targets": ["m:Unknown"]},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                start_check(
                    workspace=case["workspace"],
                    project_sha=SHA_A,
                    repositories={},
                    device=case["device"],
                    target_ids=case["targets"],
                )
        verify.assert_not_called()
        remote.assert_not_called()

    def test_remote_check_contract_uses_shared_lock_and_detached_screen(self) -> None:
        self.assertIn("/home/android/.yrrp-build-launch.lock", REMOTE_CHECK_SCRIPT)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", REMOTE_CHECK_SCRIPT)
        self.assertIn('"screen", "-L", "-Logfile"', REMOTE_CHECK_SCRIPT)
        self.assertIn('"-dmS", run_id', REMOTE_CHECK_SCRIPT)
        self.assertIn('atomic_json(result_path', REMOTE_CHECK_SCRIPT)

    def test_isolated_mounts_use_read_only_release_lower_layer(self) -> None:
        self.assertIn(
            'release_host = "/mnt/fast/docker/android/workspace"', REMOTE_CHECK_SCRIPT
        )
        self.assertIn('release_host + ":/opt/android:ro"', REMOTE_CHECK_SCRIPT)
        self.assertIn('release_host + ":/opt/release-lower:ro"', REMOTE_CHECK_SCRIPT)
        self.assertIn('check_host + "/out:/opt/android/out:rw"', REMOTE_CHECK_SCRIPT)
        self.assertIn("/mnt/fast/docker/android/ccache:/ccache:rw", REMOTE_CHECK_SCRIPT)
        self.assertIn(
            'project_host = "/mnt/fast/docker/android/project"', REMOTE_CHECK_SCRIPT
        )
        self.assertIn('project_host + ":/opt/yrrp/project:ro"', REMOTE_CHECK_SCRIPT)
        self.assertNotIn(
            "/mnt/fast/docker/android/check-workspace:/opt/android:rw",
            REMOTE_CHECK_SCRIPT,
        )

    def test_isolated_container_has_persistent_writable_home(self) -> None:
        self.assertIn('"--env", "HOME=/home/android"', REMOTE_CHECK_SCRIPT)
        self.assertIn('check_host + "/home:/home/android:rw"', REMOTE_CHECK_SCRIPT)

    def test_check_containers_disable_repo_trace_and_git_index_refresh(self) -> None:
        from yrrp_release.check_remote import REMOTE_CHECK_WORKER_SCRIPT

        containers = []
        for node in ast.walk(ast.parse(REMOTE_CHECK_WORKER_SCRIPT)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id != "run":
                    continue
                arguments = node.args
            elif isinstance(node, ast.List):
                arguments = node.elts
            else:
                continue
            values = [item.value for item in arguments if isinstance(item, ast.Constant)]
            if values[:2] == ["docker", "run"] and "--env" in values:
                containers.append(values)
        self.assertEqual(2, len(containers))
        for values in containers:
            for setting in ("REPO_TRACE=0", "GIT_OPTIONAL_LOCKS=0"):
                with self.subTest(home=values[values.index("--env") + 1], setting=setting):
                    self.assertIn(setting, values)
                    self.assertEqual("--env", values[values.index(setting) - 1])

    def test_detached_worker_disables_repo_trace_and_git_index_refresh(self) -> None:
        self.assertIn(
            '"env", "REPO_TRACE=0", "GIT_OPTIONAL_LOCKS=0",', REMOTE_CHECK_SCRIPT
        )

    def test_isolated_workspace_setup_preserves_managed_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "home").mkdir()
            (workspace / "home" / ".gitconfig").write_text("[user]\n")
            (workspace / "out").mkdir()
            (workspace / "out" / "cached-object").write_text("cached\n")
            (workspace / "repos" / "packages" / "apps" / "ThemePicker").mkdir(
                parents=True
            )
            overlay_marker = (
                workspace / "repos" / "packages" / "apps" / "ThemePicker" / "marker"
            )
            overlay_marker.write_text("overlay\n")
            command = [
                sys.executable,
                "-c",
                ISOLATED_WORKSPACE_SETUP_SCRIPT,
                str(workspace),
                str(os.getuid()),
                str(os.getgid()),
            ]
            subprocess.run(command, check=True)
            subprocess.run(command, check=True)
            self.assertEqual("[user]\n", (workspace / "home" / ".gitconfig").read_text())
            self.assertEqual("cached\n", (workspace / "out" / "cached-object").read_text())
            self.assertEqual("overlay\n", overlay_marker.read_text())
            self.assertEqual(0o700, (workspace / "home").stat().st_mode & 0o777)

    def test_manifest_remotes_fetch_exact_sha_from_real_git_layouts(self) -> None:
        functions = {
            node.name: node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef)
            and node.name in ("manifest_repository_remotes", "fetch_repository_revision")
        }

        def run(*args, cwd=None, check=True):
            return subprocess.run(
                list(args), cwd=cwd, capture_output=True, check=check, text=True
            )

        def output(*args, cwd=None):
            return run(*args, cwd=cwd).stdout.strip()

        namespace = {"ET": ET, "output": output, "re": re, "run": run}
        exec(
            compile(ast.Module(body=list(functions.values()), type_ignores=[]), "remote", "exec"),
            namespace,
        )
        manifest = """\
<manifest>
  <remote name="yrrp" fetch="https://github.com/YRRPs" />
  <remote name="private" fetch="ssh://private.example" />
  <default remote="yrrp" revision="refs/heads/lineage-23.2" />
  <project name="android_packages_apps_ThemePicker" path="packages/apps/ThemePicker" />
  <project name="android_vendor_oplus_camera-salami" path="vendor/oplus/camera-salami" remote="private" />
</manifest>
"""
        remotes = namespace["manifest_repository_remotes"](manifest)
        self.assertEqual("yrrp", remotes["packages/apps/ThemePicker"])
        self.assertEqual("private", remotes["vendor/oplus/camera-salami"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.git"
            seed = root / "seed"
            subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
            subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(seed), "config", "user.name", "Check Test"], check=True
            )
            subprocess.run(
                ["git", "-C", str(seed), "config", "user.email", "check@example.invalid"],
                check=True,
            )
            (seed / "source.txt").write_text("source\n")
            subprocess.run(["git", "-C", str(seed), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-m", "source"], check=True, capture_output=True)
            expected_sha = output("git", "rev-parse", "HEAD", cwd=seed)
            subprocess.run(
                ["git", "-C", str(seed), "push", str(upstream), "HEAD:refs/heads/main"],
                check=True,
                capture_output=True,
            )

            for remote_name in ("yrrp", "private"):
                with self.subTest(remote_name=remote_name):
                    checkout = root / f"checkout-{remote_name}"
                    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(checkout),
                            "remote",
                            "add",
                            remote_name,
                            str(upstream),
                        ],
                        check=True,
                    )
                    namespace["fetch_repository_revision"](
                        checkout, remote_name, expected_sha
                    )
                    self.assertEqual(
                        expected_sha,
                        output("git", "rev-parse", "HEAD", cwd=checkout),
                    )

    def test_overlay_repositories_reuse_objects_and_drop_stale_paths(self) -> None:
        overlay_functions = {
            "atomic_json",
            "fetch_repository_revision",
            "load_overlay_state",
            "overlay_path",
            "prepare_overlay_repositories",
            "prepare_overlay_repository",
            "remove_stale_overlays",
            "write_overlay_state",
        }
        functions = [
            node
            for node in ast.parse(ISOLATED_OVERLAY_PREP_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name in overlay_functions
        ]

        def run(*args, cwd=None, check=True):
            return subprocess.run(
                list(args), cwd=cwd, capture_output=True, check=check, text=True
            )

        def output(*args, cwd=None):
            return run(*args, cwd=cwd).stdout.strip()

        namespace = {
            "json": json,
            "os": os,
            "output": output,
            "Path": Path,
            "re": re,
            "run": run,
            "shutil": __import__("shutil"),
            "tempfile": tempfile,
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "overlay", "exec"), namespace)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lower = root / "lower"
            overlays = root / "overlays"
            active = root / "active.json"
            lower.mkdir()

            def create_repository(relative: str, remote_name: str) -> tuple[Path, Path, str]:
                upstream = root / (relative.replace("/", "-") + ".git")
                seed = root / (relative.replace("/", "-") + "-seed")
                source = lower / relative
                subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
                subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(seed), "config", "user.name", "Check Test"], check=True)
                subprocess.run(
                    ["git", "-C", str(seed), "config", "user.email", "check@example.invalid"],
                    check=True,
                )
                (seed / "source.txt").write_text(relative + "\n")
                subprocess.run(["git", "-C", str(seed), "add", "source.txt"], check=True)
                subprocess.run(["git", "-C", str(seed), "commit", "-m", "source"], check=True, capture_output=True)
                sha = output("git", "rev-parse", "HEAD", cwd=seed)
                subprocess.run(
                    ["git", "-C", str(seed), "push", str(upstream), "HEAD:refs/heads/main"],
                    check=True,
                    capture_output=True,
                )
                source.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(source), "remote", "add", remote_name, str(upstream)],
                    check=True,
                )
                subprocess.run(["git", "-C", str(source), "fetch", remote_name, sha], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(source), "checkout", "--detach", sha], check=True, capture_output=True)
                return upstream, seed, sha

            theme_upstream, theme_seed, theme_sha = create_repository(
                "packages/apps/ThemePicker", "yrrp"
            )
            _, _, camera_sha = create_repository("vendor/oplus/camera-salami", "private")
            prepare = namespace["prepare_overlay_repositories"]
            prepare(
                {
                    "packages/apps/ThemePicker": theme_sha,
                    "vendor/oplus/camera-salami": camera_sha,
                },
                {
                    "packages/apps/ThemePicker": "yrrp",
                    "vendor/oplus/camera-salami": "private",
                },
                lower,
                overlays,
                active,
            )
            theme_overlay = overlays / "packages/apps/ThemePicker"
            camera_overlay = overlays / "vendor/oplus/camera-salami"
            git_inode = (theme_overlay / ".git").stat().st_ino
            self.assertEqual(theme_sha, output("git", "rev-parse", "HEAD", cwd=theme_overlay))
            self.assertEqual(camera_sha, output("git", "rev-parse", "HEAD", cwd=camera_overlay))

            (theme_seed / "source.txt").write_text("updated\n")
            subprocess.run(["git", "-C", str(theme_seed), "commit", "-am", "updated"], check=True, capture_output=True)
            updated_sha = output("git", "rev-parse", "HEAD", cwd=theme_seed)
            subprocess.run(
                ["git", "-C", str(theme_seed), "push", str(theme_upstream), "HEAD:refs/heads/main"],
                check=True,
                capture_output=True,
            )
            prepare(
                {"packages/apps/ThemePicker": updated_sha},
                {"packages/apps/ThemePicker": "yrrp"},
                lower,
                overlays,
                active,
            )
            self.assertEqual(git_inode, (theme_overlay / ".git").stat().st_ino)
            self.assertEqual(updated_sha, output("git", "rev-parse", "HEAD", cwd=theme_overlay))
            self.assertFalse(camera_overlay.exists())
            self.assertEqual(
                ["packages/apps/ThemePicker"], json.loads(active.read_text())
            )

    def test_failed_overlay_preparation_is_cleaned_by_next_request(self) -> None:
        names = {
            "atomic_json", "overlay_path", "load_overlay_state",
            "write_overlay_state", "remove_stale_overlays",
            "prepare_overlay_repositories",
        }
        functions = [
            node for node in ast.parse(ISOLATED_OVERLAY_PREP_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        namespace = {
            "json": json, "os": os, "Path": Path, "re": re,
            "shutil": __import__("shutil"), "tempfile": tempfile,
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "overlay", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlays = root / "repos"
            overlays.mkdir()
            active = root / "active.json"

            def prepare_repository(source, overlay, remote, sha):
                overlay.mkdir(parents=True, exist_ok=True)
                (overlay / "partial").write_text(sha)
                if overlay.name == "b":
                    raise RuntimeError("injected fetch failure")

            namespace["prepare_overlay_repository"] = prepare_repository
            prepare = namespace["prepare_overlay_repositories"]
            with self.assertRaisesRegex(RuntimeError, "injected fetch failure"):
                prepare({"a": SHA_A, "b": SHA_B}, {"a": "yrrp", "b": "yrrp"}, root, overlays, active)
            prepare({"c": SHA_A}, {"c": "yrrp"}, root, overlays, active)
            self.assertEqual(["c"], sorted(path.name for path in overlays.iterdir()))
            self.assertEqual(["c"], json.loads(active.read_text()))

    def test_overlay_cleanup_failure_preserves_previous_inventory(self) -> None:
        names = {
            "atomic_json", "overlay_path", "load_overlay_state",
            "write_overlay_state", "prepare_overlay_repositories",
        }
        functions = [
            node for node in ast.parse(ISOLATED_OVERLAY_PREP_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        namespace = {
            "json": json, "os": os, "Path": Path, "re": re,
            "tempfile": tempfile,
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "overlay", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.json"
            active.write_text(json.dumps(["old"]))

            def fail_cleanup(previous, requested, overlay_root):
                raise OSError("injected cleanup failure")

            namespace["remove_stale_overlays"] = fail_cleanup
            with self.assertRaisesRegex(OSError, "injected cleanup failure"):
                namespace["prepare_overlay_repositories"](
                    {"new": SHA_A}, {"new": "yrrp"}, root, root / "repos", active
                )
            self.assertEqual(["old"], json.loads(active.read_text()))

    def test_isolated_inner_uses_prepared_overlays_without_repo_sync(self) -> None:
        self.assertIn(
            "perform_check(payload, run_id, synchronize=False, lower_android=LOWER_ANDROID)",
            REMOTE_CHECK_INNER_SCRIPT,
        )
        self.assertIn(
            'check_host + "/repos/" + relative + ":/opt/android/" + relative + ":rw"',
            REMOTE_CHECK_SCRIPT,
        )

    def test_isolated_check_fingerprints_release_lower_layer_before_and_after(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "perform_check"
        )
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "release_tree_fingerprint"
        ]
        self.assertEqual(2, len(calls))
        self.assertIn(
            "release checkout is not clean at manifest revisions",
            REMOTE_CHECK_INNER_SCRIPT,
        )
        self.assertIn(
            "release lower layer changed during isolated check",
            REMOTE_CHECK_INNER_SCRIPT,
        )

    def test_result_parser_accepts_interleaved_request_order(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "parse_target_results"
        )
        registry = {
            "m:SystemUI": ["m", "SystemUI"],
            "atest:SystemUITests_presubmit": ["atest", "SystemUITests_presubmit"],
        }
        pattern = re.compile(
            r"^[^:\n]+: Passed: (?P<passed>[0-9]+), Failed: (?P<failed>[0-9]+), "
            r"Ignored: (?P<ignored>[0-9]+)$",
            re.MULTILINE,
        )
        result_pattern = re.compile(r"\b(?P<outcome>PASSED|FAILED)\s*$", re.MULTILINE)
        namespace = {
            "Path": Path,
            "TARGET_REGISTRY": registry,
            "ATEST_PATTERN": pattern,
            "ATEST_RESULT_LINE_PATTERN": result_pattern,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compile.log").write_text("compiled\n")
            (root / "atest.log").write_text(
                "SystemUITests_presubmit: Passed: 2, Failed: 0, Ignored: 0\n"
            )
            (root / "target-results.tsv").write_text(
                "m\tm:SystemUI\t0\t2026-10-15T12:00:00Z\t2026-10-15T12:01:00Z\t"
                + str(root / "compile.log")
                + "\natest\tatest:SystemUITests_presubmit\t0\t2026-10-15T12:01:00Z\t"
                "2026-10-15T12:02:00Z\t"
                + str(root / "atest.log")
                + "\n"
            )
            results = namespace["parse_target_results"](
                root,
                ["atest:SystemUITests_presubmit", "m:SystemUI"],
            )
        self.assertEqual(2, len(results))
        self.assertTrue(all(result["result"] == "passed" for result in results))
        self.assertEqual(2, results[1]["passed_tests"])
        self.assertEqual(0, results[1]["failed_tests"])
        self.assertEqual(0, results[1]["ignored_tests"])

    def test_result_parser_records_failed_atest_execution(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "parse_target_results"
        )
        registry = {"atest:ThemePickerRoboTests": ["atest", "ThemePickerRoboTests"]}
        pattern = re.compile(
            r"^(?:[^:\n]+:\s*)?Passed:\s*(?P<passed>[0-9]+),\s*"
            r"Failed:\s*(?P<failed>[0-9]+)"
            r"(?:,\s*Ignored:\s*(?P<ignored>[0-9]+))?$",
            re.MULTILINE,
        )
        namespace = {
            "Path": Path,
            "TARGET_REGISTRY": registry,
            "ATEST_PATTERN": pattern,
            "ATEST_RESULT_LINE_PATTERN": re.compile(
                r"\b(?P<outcome>PASSED|FAILED)\s*$", re.MULTILINE
            ),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_path = root / "atest.log"
            output_path.write_text(FAILED_MODULES)
            (root / "target-results.tsv").write_text(
                "atest\tatest:ThemePickerRoboTests\t2\t2026-10-15T12:00:00Z\t"
                "2026-10-15T12:01:00Z\t" + str(output_path) + "\n"
            )
            result = namespace["parse_target_results"](
                root, ["atest:ThemePickerRoboTests"]
            )[0]
        self.assertEqual("failed", result["result"])
        self.assertEqual(215, result["executed_tests"])
        self.assertEqual(16, result["passed_tests"])
        self.assertEqual(199, result["failed_tests"])

    def test_zero_exit_atest_failures_cannot_publish_passed_evidence(self) -> None:
        function = next(
            node for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "parse_target_results"
        )
        from yrrp_release.checks import ATEST_SUMMARY_PATTERN, ATEST_RESULT_LINE_PATTERN

        namespace = {
            "Path": Path,
            "TARGET_REGISTRY": {"atest:ThemePickerRoboTests": ["atest", "ThemePickerRoboTests"]},
            "ATEST_PATTERN": ATEST_SUMMARY_PATTERN,
            "ATEST_RESULT_LINE_PATTERN": ATEST_RESULT_LINE_PATTERN,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        for output in (FAILED_MODULES, FAILED_LINES):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "atest.log"
                log.write_text(output)
                (root / "target-results.tsv").write_text(
                    "atest\tatest:ThemePickerRoboTests\t0\t2026-10-15T12:00:00Z\t"
                    "2026-10-15T12:01:00Z\t" + str(log) + "\n"
                )
                target = namespace["parse_target_results"](
                    root, ["atest:ThemePickerRoboTests"]
                )[0]
                self.assertEqual("failed", target["result"])
                self.assertNotEqual(0, target["exit_code"])
                self.assertGreater(target["failed_tests"], 0)
                document = valid_result()
                document["targets"] = [target]
                document["result"] = "failed"
                document["exit_code"] = target["exit_code"]
                document["failure_reason"] = "atest reported failed tests"
                self.assertEqual("failed", validate_check_result(document)["result"])

    def test_isolated_exit_without_result_creates_failure_evidence(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_isolated_result"
        )

        def atomic_json(path, document, exclusive=False):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document))

        namespace = {
            "atomic_json": atomic_json,
            "json": json,
            "utc_now": lambda: "2026-10-15T12:00:00Z",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "workspace": "isolated",
                "device": "salami",
                "project_sha": SHA_A,
                "repositories": {},
            }
            namespace["ensure_isolated_result"](payload, RUN_ID, 17, root)
            result = json.loads((root / RUN_ID / "result.json").read_text())
            status = json.loads((root / RUN_ID / "status.json").read_text())
        self.assertEqual("failed", result["result"])
        self.assertEqual(17, result["exit_code"])
        self.assertIn("without-result", result["failure_reason"])
        self.assertEqual(
            {
                "run_id": RUN_ID,
                "phase": "failed",
                "result": "failed",
                "exit_code": 17,
                "failure_reason": "isolated-check-exit-17-without-result",
            },
            status,
        )

    def test_existing_isolated_result_replaces_pending_status(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_isolated_result"
        )

        def atomic_json(path, document, exclusive=False):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document))

        namespace = {
            "atomic_json": atomic_json,
            "json": json,
            "utc_now": lambda: "2026-10-15T12:00:00Z",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / RUN_ID
            run_directory.mkdir()
            (run_directory / "result.json").write_text(
                json.dumps(
                    {
                        "run_id": RUN_ID,
                        "result": "passed",
                        "exit_code": 0,
                        "failure_reason": None,
                    }
                )
            )
            (run_directory / "status.json").write_text(
                json.dumps(
                    {"run_id": RUN_ID, "phase": "targets", "result": "pending"}
                )
            )
            created = namespace["ensure_isolated_result"](
                {"workspace": "isolated"}, RUN_ID, 0, root
            )
            status = json.loads((run_directory / "status.json").read_text())
        self.assertFalse(created)
        self.assertEqual(
            {
                "run_id": RUN_ID,
                "phase": "complete",
                "result": "passed",
                "exit_code": 0,
            },
            status,
        )

    def test_isolated_result_create_race_uses_immutable_winner(self) -> None:
        function = next(
            node
            for node in ast.parse(REMOTE_CHECK_INNER_SCRIPT).body
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_isolated_result"
        )
        winner = {
            "run_id": RUN_ID,
            "result": "passed",
            "exit_code": 0,
            "failure_reason": None,
        }

        def atomic_json(path, document, exclusive=False):
            path.parent.mkdir(parents=True, exist_ok=True)
            if exclusive:
                path.write_text(json.dumps(winner))
                raise FileExistsError(path)
            path.write_text(json.dumps(document))

        namespace = {
            "atomic_json": atomic_json,
            "json": json,
            "utc_now": lambda: "2026-10-15T12:00:00Z",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = namespace["ensure_isolated_result"](
                {
                    "workspace": "isolated",
                    "device": "salami",
                    "project_sha": SHA_A,
                    "repositories": {},
                },
                RUN_ID,
                1,
                root,
            )
            status = json.loads((root / RUN_ID / "status.json").read_text())
        self.assertFalse(created)
        self.assertEqual(
            {
                "run_id": RUN_ID,
                "phase": "complete",
                "result": "passed",
                "exit_code": 0,
            },
            status,
        )

    def test_target_script_sources_environment_once_without_set_u(self) -> None:
        self.assertEqual(1, CHECK_TARGET_SCRIPT.count("source build/envsetup.sh"))
        self.assertEqual(1, CHECK_TARGET_SCRIPT.count("breakfast"))
        self.assertNotIn("set -u", CHECK_TARGET_SCRIPT)
        self.assertIn('run_one m "${compile_csv}"', CHECK_TARGET_SCRIPT)
        self.assertIn('atest "${atest_targets[${suite_index}]}"', CHECK_TARGET_SCRIPT)


if __name__ == "__main__":
    unittest.main()

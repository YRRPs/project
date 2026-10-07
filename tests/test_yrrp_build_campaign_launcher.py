from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign import launcher
from yrrp_build_campaign.launcher import (
    REMOTE_LAUNCH_SCRIPT,
    REMOTE_SNAPSHOT_SCRIPT,
    collect_remote_snapshot,
    collect_remote_source,
    launch_campaign,
    launch_remote_build,
)
from yrrp_build_campaign.model import CampaignState, digest_json
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


MANIFEST = b"<manifest/>\n"

REAL_SUBPROCESS_RUN = subprocess.run

STAND_IN_SCRIPT = r'''
import json
import sys

payload = sys.stdin.read()
marker = "it's \"quoted\" $HOME `true` ; exit 3"
print(json.dumps({
    "argv": sys.argv,
    "stdin": payload,
    "marker": marker,
    "manifest_xml": "<manifest/>",
}))
'''


def command_ref(step_id: str) -> dict:
    return {
        "check_id": step_id,
        "command_sha256": "f" * 64,
        "location": "device",
    }


def device_case() -> dict:
    return {
        "case_id": "pulse-nav",
        "setup_checks": [],
        "action_id": "exercise-pulse",
        "expected": "Pulse renders as configured",
        "cleanup_checks": [command_ref("restore-pulse")],
    }


def frozen_snapshot() -> dict:
    return {
        "manifest_sha256": hashlib.sha256(MANIFEST).hexdigest(),
        "manifest_evidence": "evidence/october-batch/manifest.xml",
        "repositories": {"frameworks/base": "a" * 40},
        "branches": {"frameworks/base": "lineage-23.2"},
        "clean_repositories": ["frameworks/base"],
        "project_sha": "e" * 40,
        "build_mode": "signed-ota",
        "matrix_sha256": digest_json(
            {"pulse": [device_case()]}
        ),
        "concern_inventory_sha256": digest_json(
            {
                "pulse": {
                    "repositories": ["frameworks/base"],
                    "revisions": {"frameworks/base": "a" * 40},
                }
            }
        ),
    }


class CampaignLauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = CampaignStore(Path(self.temp.name) / "campaigns")
        self.service = CampaignService(self.store)
        self.service.create("october-batch")
        self.store.write_evidence("october-batch", "manifest.xml", MANIFEST)
        self.service.register_feature(
            "october-batch",
            {
                "feature_id": "pulse",
                "phase": "IMPLEMENTING",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": [
                    {
                        "check_id": "systemui-module",
                        "command_sha256": "f" * 64,
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
            },
            sender="feature-session",
        )
        self.service.mark_feature_ready(
            "october-batch",
            "pulse",
            {
                "revisions": {"frameworks/base": "a" * 40},
                "clean_repositories": ["frameworks/base"],
                "check_evidence": ["preflight.txt"],
                "observability": "ready",
                "restoration_steps": [command_ref("restore-pulse")],
            },
        )
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)
        self.service.freeze(
            "october-batch",
            frozen_snapshot(),
            "Freeze and build",
            actor="yrrp-build-campaign",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def authorize(self) -> None:
        self.service.authorize_launcher(
            "october-batch",
            actor="session-test",
            agent_type="yrrp-build-campaign",
        )

    def test_launcher_requires_one_time_authorization(self) -> None:
        with self.assertRaises(ValueError):
            launch_campaign(
                self.service,
                "october-batch",
                snapshot_provider=lambda _: frozen_snapshot(),
                build_runner=lambda *_: None,
            )
        self.assertEqual(
            CampaignState.FROZEN,
            self.store.load("october-batch").state,
        )

    def test_launcher_verifies_snapshot_claims_and_runs(self) -> None:
        self.authorize()
        runs = []

        launch_campaign(
            self.service,
            "october-batch",
            snapshot_provider=lambda _: frozen_snapshot(),
            build_runner=lambda *_: runs.append("launched"),
        )

        campaign = self.store.load("october-batch")
        self.assertEqual(["launched"], runs)
        self.assertEqual(CampaignState.BUILDING, campaign.state)
        self.assertEqual("session-test", campaign.build_attempts[-1]["actor"])
        self.assertEqual([], campaign.launcher_authorizations)

    def test_launcher_rejects_stale_source_before_running(self) -> None:
        self.authorize()
        runs = []
        stale = frozen_snapshot()
        stale["repositories"] = {"frameworks/base": "9" * 40}

        with self.assertRaises(ValueError):
            launch_campaign(
                self.service,
                "october-batch",
                snapshot_provider=lambda _: stale,
                build_runner=lambda *_: runs.append("launched"),
            )

        self.assertEqual([], runs)
        self.assertEqual(
            CampaignState.FROZEN,
            self.store.load("october-batch").state,
        )

    def test_launch_failure_enters_fix_batch(self) -> None:
        self.authorize()

        def fail(*_) -> None:
            raise RuntimeError("ssh launch failed")

        with self.assertRaisesRegex(RuntimeError, "ssh launch failed"):
            launch_campaign(
                self.service,
                "october-batch",
                snapshot_provider=lambda _: frozen_snapshot(),
                build_runner=fail,
            )

        campaign = self.store.load("october-batch")
        self.assertEqual(CampaignState.FIX_BATCH_READY, campaign.state)
        self.assertEqual("launch-failed", campaign.build_attempts[-1]["status"])

    def test_remote_build_holds_checkout_lock_for_full_process(self) -> None:
        self.assertIn('"flock", "-x"', REMOTE_LAUNCH_SCRIPT)
        self.assertIn(".yrrp-build-launch.lock", REMOTE_LAUNCH_SCRIPT)

    @patch("yrrp_build_campaign.launcher.subprocess.run")
    def test_remote_commands_use_fixed_argument_vectors(self, run) -> None:
        run.return_value.stdout = '{"manifest_sha256":"b", "manifest_xml":"<manifest/>", "repositories":{}, "branches":{}, "clean_repositories":[], "project_sha":"e"}'
        campaign = self.store.load("october-batch")

        source, manifest = collect_remote_source(campaign)
        collect_remote_snapshot(campaign)
        launch_remote_build("october-batch", frozen_snapshot())

        self.assertEqual("<manifest/>", manifest)
        self.assertEqual("b", source["manifest_sha256"])
        for call in run.call_args_list:
            args, kwargs = call
            self.assertIsInstance(args[0], list)
            self.assertFalse(kwargs.get("shell", False))


    def capture_remote_argvs(self) -> list[tuple[list[str], str]]:
        captured: list[tuple[list[str], str]] = []

        def fake_run(argv, **kwargs):
            captured.append((argv, kwargs["input"]))
            result = subprocess.CompletedProcess(argv, 0)
            result.stdout = '{"manifest_xml": "<manifest/>"}'
            return result

        campaign = self.store.load("october-batch")
        with patch("yrrp_build_campaign.launcher.subprocess.run", fake_run):
            collect_remote_source(campaign)
            launch_remote_build("october-batch", frozen_snapshot())
        return captured

    def test_remote_python_survives_ssh_argument_join(self) -> None:
        captured = self.capture_remote_argvs()

        expected_scripts = [REMOTE_SNAPSHOT_SCRIPT, REMOTE_LAUNCH_SCRIPT]
        self.assertEqual(2, len(captured))
        for (argv, _), script in zip(captured, expected_scripts):
            # ssh joins the remote argv with spaces; the remote shell re-parses it.
            remote_command = " ".join(argv[2:])
            self.assertEqual(["python3", "-c", script], shlex.split(remote_command))

    def test_remote_python_runs_unchanged_through_shell(self) -> None:
        with patch.object(launcher, "REMOTE_SNAPSHOT_SCRIPT", STAND_IN_SCRIPT), \
                patch.object(launcher, "REMOTE_LAUNCH_SCRIPT", STAND_IN_SCRIPT):
            captured = self.capture_remote_argvs()

        self.assertEqual(2, len(captured))
        for argv, payload in captured:
            remote_command = " ".join(argv[2:])
            result = REAL_SUBPROCESS_RUN(
                ["bash", "-c", remote_command],
                input=payload,
                capture_output=True,
                check=True,
                text=True,
            )
            output = json.loads(result.stdout)
            self.assertEqual(["-c"], output["argv"])
            self.assertEqual(payload, output["stdin"])
            self.assertEqual(
                "it's \"quoted\" $HOME `true` ; exit 3", output["marker"]
            )


BUILD_PROCESS_FUNCTION = re.compile(
    r"^def build_process_active\(.*?(?=^\S)", re.MULTILINE | re.DOTALL
)

ACTIVE_BUILD_LINES = [
    "/opt/android/prebuilts/build-tools/linux-x86/bin/ninja -f out/combined.ninja",
    "/bin/bash build/soong/soong_ui.bash --make-mode",
    "python3 /opt/android/out/host/linux-x86/bin/ota_from_target_files -k key a b",
    "/opt/android/out/host/linux-x86/bin/sign_target_files_apks -o in out",
]

INACTIVE_LINES = [
    "python3 -c 'import x  # soong_ui ninja ota_from_target_files "
    "sign_target_files_apks'",
    "bash -c \"python3 -c '...soong_ui ninja ota_from_target_files "
    "sign_target_files_apks...'\"",
    "grep ninja",
    "vim ninja.txt",
]


def build_process_function_source(script: str) -> str:
    match = BUILD_PROCESS_FUNCTION.search(script)
    if match is None:
        raise AssertionError("script has no build_process_active function")
    return match.group(0)


def load_build_process_active(script: str):
    namespace: dict = {}
    exec(build_process_function_source(script), namespace)
    return namespace["build_process_active"]


SELF_MATCH_CHILD = r"""
import json
from pathlib import Path

own = Path("/proc/self/cmdline").read_bytes().rstrip(b"\0").replace(b"\0", b" ")
own_line = own.decode()
parent_line = "bash -c " + chr(34) + own_line + chr(34)
fixture = "COMMAND\n" + own_line + "\n" + parent_line + "\n"
names = ("soong_ui", "ninja", "ota_from_target_files", "sign_target_files_apks")
print(json.dumps({
    "own_line_has_all_names": all(name in own_line for name in names),
    "active": build_process_active(fixture),
}))
"""


class BuildProcessDetectionTest(unittest.TestCase):
    SCRIPTS = {
        "snapshot": REMOTE_SNAPSHOT_SCRIPT,
        "launch": REMOTE_LAUNCH_SCRIPT,
    }

    def test_both_scripts_share_identical_build_process_function(self) -> None:
        self.assertEqual(
            build_process_function_source(REMOTE_SNAPSHOT_SCRIPT),
            build_process_function_source(REMOTE_LAUNCH_SCRIPT),
        )

    def test_both_scripts_check_ps_args_with_build_process_function(self) -> None:
        for label, script in self.SCRIPTS.items():
            with self.subTest(script=label):
                self.assertIn(
                    'build_process_active(run("ps", "-eo", "args", cwd=ANDROID))',
                    script,
                )
                self.assertNotIn("in processes for name in", script)

    def test_build_programs_count_as_active(self) -> None:
        for label, script in self.SCRIPTS.items():
            active = load_build_process_active(script)
            for line in ACTIVE_BUILD_LINES:
                with self.subTest(script=label, line=line):
                    self.assertTrue(active("COMMAND\n" + line + "\n"))

    def test_command_lines_merely_mentioning_names_are_inactive(self) -> None:
        for label, script in self.SCRIPTS.items():
            active = load_build_process_active(script)
            for line in INACTIVE_LINES:
                with self.subTest(script=label, line=line):
                    self.assertFalse(active("COMMAND\n" + line + "\n"))

    def test_check_does_not_match_its_own_python_c_process(self) -> None:
        for label, script in self.SCRIPTS.items():
            with self.subTest(script=label):
                child = (
                    build_process_function_source(script) + SELF_MATCH_CHILD
                )
                result = REAL_SUBPROCESS_RUN(
                    ["python3", "-c", child],
                    capture_output=True,
                    check=True,
                    text=True,
                )
                output = json.loads(result.stdout)
                self.assertTrue(output["own_line_has_all_names"])
                self.assertFalse(output["active"])


if __name__ == "__main__":
    unittest.main()

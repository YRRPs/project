from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "yrrp-build-campaign.py"


def device_case() -> dict:
    return {
        "case_id": "pulse-nav",
        "setup_checks": [],
        "action_id": "exercise-pulse",
        "expected": "Pulse renders as configured",
        "cleanup_checks": [
            {
                "check_id": "restore-pulse",
                "command_sha256": "f" * 64,
                "location": "device",
            }
        ],
    }


class CampaignCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "campaigns"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, payload: dict | None = None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=None if payload is None else json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ | {"YRRP_CAMPAIGN_ROOT": str(self.root)},
        )

    def create(self) -> None:
        result = self.run_cli("create", "--campaign-id", "october-batch")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_create_prints_machine_readable_status(self) -> None:
        self.create()

        result = self.run_cli("status", "--campaign-id", "october-batch")
        value = json.loads(result.stdout)

        self.assertEqual("october-batch", value["campaign_id"])
        self.assertEqual("COLLECTING", value["state"])
        self.assertEqual(
            {"PASS": 0, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0},
            value["device_matrix"],
        )

    def test_register_reads_payload_from_stdin(self) -> None:
        self.create()
        payload = {
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
        }

        result = self.run_cli(
            "register-feature",
            "--campaign-id",
            "october-batch",
            "--sender",
            "feature-session",
            payload=payload,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        state = json.loads((self.root / "october-batch.json").read_text())
        self.assertEqual("feature-session", state["features"]["pulse"]["sender"])

    def test_invalid_transition_returns_nonzero_without_traceback(self) -> None:
        self.create()

        result = self.run_cli(
            "transition",
            "--campaign-id",
            "october-batch",
            "--to",
            "FROZEN",
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("guarded state requires dedicated operation", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_raw_command_is_not_written_to_state(self) -> None:
        self.create()
        command = "ssh AndroidBuilder 'm SystemUI'"
        digest = hashlib.sha256(command.encode()).hexdigest()
        registration = {
            "feature_id": "pulse",
            "phase": "IMPLEMENTING",
            "spec": "docs/spec.md",
            "plan": "docs/plan.md",
            "repositories": ["frameworks/base"],
            "cheap_checks": [
                {
                    "check_id": "systemui-module",
                    "command_sha256": digest,
                    "location": "AndroidBuilder",
                }
            ],
            "device_cases": [device_case()],
        }
        result = self.run_cli(
            "register-feature",
            "--campaign-id",
            "october-batch",
            "--sender",
            "feature-session",
            payload=registration,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        for state in ("IMPLEMENTING", "PREFLIGHT"):
            result = self.run_cli(
                "transition",
                "--campaign-id",
                "october-batch",
                "--to",
                state,
            )
            self.assertEqual(0, result.returncode, result.stderr)
        result = self.run_cli(
            "authorize-preflight",
            "--campaign-id",
            "october-batch",
            payload={"command": command},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        state = (self.root / "october-batch.json").read_text()
        self.assertNotIn(command, state)
        self.assertIn(json.loads(result.stdout)["command_sha256"], state)

    def test_attach_evidence_rejects_sensitive_content(self) -> None:
        self.create()

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "attach-evidence",
                "--campaign-id",
                "october-batch",
                "--name",
                "preflight.txt",
            ],
            input="password=secret\n",
            capture_output=True,
            text=True,
            env=os.environ | {"YRRP_CAMPAIGN_ROOT": str(self.root)},
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("sensitive material", result.stderr)

    def test_authorize_recovery_stores_only_digest(self) -> None:
        self.create()
        state_path = self.root / "october-batch.json"
        campaign = json.loads(state_path.read_text())
        campaign["build_attempts"] = [{"build_id": "20261008-090000"}]
        state_path.write_text(json.dumps(campaign))
        command = (
            "ssh AndroidBuilder /opt/yrrp/project/scripts/generate-incremental-ota.sh "
            "--source-build 20261007-083337 --target-build 20261008-090000"
        )
        result = self.run_cli(
            "authorize-recovery", "--campaign-id", "october-batch", payload={"command": command}
        )
        self.assertEqual(0, result.returncode, result.stderr)
        state = (self.root / "october-batch.json").read_text()
        self.assertNotIn(command, state)
        self.assertIn(json.loads(result.stdout)["command_sha256"], state)


if __name__ == "__main__":
    unittest.main()

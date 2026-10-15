from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_ota.channel import GAPPS
from yrrp_release.deployment_record import deployment_record_from_receipt
from yrrp_release.rollback import (
    REMOTE_ROLLBACK_SCRIPT,
    rollback_approval,
    start_rollback,
)
from yrrp_release.rollback_remote import _REMOTE_JOB_BODY

BUILD_ID = "20261013-151101"
SHA = "a" * 40
DIGEST = "b" * 64
RUN_ID = "rollback-20261015-123456-a1b2c3d4"


class LegacyReceiptTest(unittest.TestCase):
    def test_normalizes_expected_artifact_identity_without_paths(self) -> None:
        receipt = {
            "release_identity": {"channel": GAPPS.name, "build_id": BUILD_ID},
            "artifacts": [
                {
                    "name": GAPPS.full_ota_name(BUILD_ID),
                    "size": 10,
                    "sha256": "a" * 64,
                    "checksum_verified": True,
                },
                {
                    "name": GAPPS.target_files_name(BUILD_ID),
                    "size": 20,
                    "sha256": "b" * 64,
                    "checksum_verified": True,
                },
                {
                    "name": GAPPS.checksums_name(BUILD_ID),
                    "size": 30,
                    "sha256": "c" * 64,
                    "checksum_verified": True,
                },
            ],
        }
        normalized = deployment_record_from_receipt(receipt, GAPPS, BUILD_ID)
        self.assertEqual(GAPPS.name, normalized["channel"])
        self.assertEqual(BUILD_ID, normalized["build_id"])
        self.assertEqual(
            [
                GAPPS.checksums_name(BUILD_ID),
                GAPPS.full_ota_name(BUILD_ID),
                GAPPS.target_files_name(BUILD_ID),
            ],
            sorted(normalized["artifacts"]),
        )
        self.assertNotIn("path", str(normalized))
        receipt["artifacts"][0]["checksum_verified"] = False
        with self.assertRaises(ValueError):
            deployment_record_from_receipt(receipt, GAPPS, BUILD_ID)


class RollbackLaunchTest(unittest.TestCase):
    def test_formats_exact_approval(self) -> None:
        self.assertEqual(
            "Rollback salami/gapps to 20261013-151101",
            rollback_approval(GAPPS, BUILD_ID),
        )

    @patch("yrrp_release.rollback.run_remote")
    @patch("yrrp_release.rollback.local_project_sha")
    def test_sends_selectors_without_artifact_paths(self, project_sha, remote) -> None:
        project_sha.return_value = SHA
        remote.return_value = {
            "run_id": RUN_ID,
            "channel": GAPPS.name,
            "build_id": BUILD_ID,
            "status": f"/opt/android/out/yrrp-rollbacks/{RUN_ID}/status.json",
            "log": f"/opt/android/out/yrrp-rollbacks/{RUN_ID}/job.log",
        }
        result = start_rollback(
            approval=rollback_approval(GAPPS, BUILD_ID),
            channel=GAPPS.name,
            build_id=BUILD_ID,
        )
        self.assertEqual(RUN_ID, result["run_id"])
        payload = remote.call_args.args[1]
        self.assertEqual(
            {
                "project_sha": SHA,
                "channel": GAPPS.name,
                "build_id": BUILD_ID,
                "legacy_record": None,
            },
            payload,
        )
        self.assertNotIn("/opt/android/out/signed", str(payload))

    @patch("yrrp_release.rollback.run_remote")
    @patch("yrrp_release.rollback.local_project_sha")
    def test_rejects_approval_variations_before_remote_access(self, project_sha, remote) -> None:
        for approval in (
            "rollback salami/gapps to 20261013-151101",
            "Rollback salami/gapps to 20261013-151101 ",
            "Rollback salami/vanilla to 20261013-151101",
        ):
            with self.subTest(approval=approval), self.assertRaises(ValueError):
                start_rollback(
                    approval=approval,
                    channel=GAPPS.name,
                    build_id=BUILD_ID,
                )
        project_sha.assert_not_called()
        remote.assert_not_called()

    def test_legacy_artifacts_use_bounded_hashing(self) -> None:
        self.assertNotIn("path.read_bytes()", _REMOTE_JOB_BODY)
        self.assertIn("for chunk in iter", _REMOTE_JOB_BODY)

    def test_remote_contract_holds_shared_lock_and_uses_fixed_deployer(self) -> None:
        self.assertIn("/home/android/.yrrp-build-launch.lock", REMOTE_ROLLBACK_SCRIPT)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", REMOTE_ROLLBACK_SCRIPT)
        self.assertIn(
            "/opt/yrrp/project/scripts/deploy-ota-release.sh",
            REMOTE_ROLLBACK_SCRIPT,
        )
        self.assertIn('"--channel", channel.name', REMOTE_ROLLBACK_SCRIPT)
        self.assertIn('"--build-id", build_id', REMOTE_ROLLBACK_SCRIPT)
        self.assertIn('"screen", "-L", "-Logfile"', REMOTE_ROLLBACK_SCRIPT)


if __name__ == "__main__":
    unittest.main()

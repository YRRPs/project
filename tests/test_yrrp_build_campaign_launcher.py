from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.launcher import (
    collect_remote_snapshot,
    collect_remote_source,
    launch_campaign,
    launch_remote_build,
)
from yrrp_build_campaign.model import CampaignState, digest_json
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


def frozen_snapshot() -> dict:
    return {
        "manifest_sha256": "b" * 64,
        "manifest_evidence": "evidence/manifest.xml",
        "repositories": {"frameworks/base": "a" * 40},
        "branches": {"frameworks/base": "lineage-23.2"},
        "clean_repositories": ["frameworks/base"],
        "project_sha": "e" * 40,
        "build_mode": "signed-ota",
        "matrix_sha256": digest_json(
            {"pulse": [{"case_id": "pulse-nav"}]}
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
                "device_cases": [{"case_id": "pulse-nav"}],
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
                "restoration_steps": ["restore Pulse settings"],
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


if __name__ == "__main__":
    unittest.main()

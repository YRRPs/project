from __future__ import annotations

import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.model import CampaignState, FeaturePhase
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


def registration(feature_id: str = "pulse") -> dict:
    return {
        "feature_id": feature_id,
        "phase": "IMPLEMENTING",
        "spec": f"docs/{feature_id}-spec.md",
        "plan": f"docs/{feature_id}-plan.md",
        "repositories": [f"repo/{feature_id}"],
        "cheap_checks": [f"m {feature_id}"],
        "device_cases": [{"case_id": f"{feature_id}-case"}],
    }


def readiness(feature_id: str = "pulse", revision: str = "a") -> dict:
    repository = f"repo/{feature_id}"
    return {
        "revisions": {repository: revision * 40},
        "clean_repositories": [repository],
        "check_evidence": [f"evidence/{feature_id}-preflight.txt"],
        "observability": "ready",
    }


def register_worker(root: str, feature_id: str, sender: str, start) -> None:
    service = CampaignService(CampaignStore(Path(root)))
    start.wait()
    service.register_feature(
        "october-batch",
        registration(feature_id),
        sender=sender,
    )


class CampaignServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = CampaignStore(Path(self.temp.name) / "campaigns")
        self.service = CampaignService(self.store)
        self.service.create("october-batch")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register_and_ready(self, feature_id: str = "pulse") -> None:
        self.service.register_feature(
            "october-batch",
            registration(feature_id),
            sender=f"{feature_id}-session",
        )
        self.service.mark_feature_ready(
            "october-batch",
            feature_id,
            readiness(feature_id),
        )

    def advance_to_frozen(self) -> None:
        self.register_and_ready()
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)
        self.service.freeze(
            "october-batch",
            {
                "manifest_sha256": "b" * 64,
                "repositories": {"repo/pulse": "a" * 40},
                "matrix_sha256": "c" * 64,
            },
            "Freeze and build",
        )

    def advance_to_testing(self) -> None:
        self.advance_to_frozen()
        self.service.claim_build(
            "october-batch",
            "ssh AndroidBuilder 'brunch salami'",
        )
        self.service.record_build_result(
            "october-batch",
            {"status": "complete", "evidence": ["build.txt"]},
        )

    def test_register_uses_cross_session_sender_address(self) -> None:
        self.service.register_feature(
            "october-batch",
            registration(),
            sender="feature-session",
        )

        stored = self.store.load("october-batch")
        self.assertEqual("feature-session", stored.features["pulse"].sender)

    def test_ready_rejects_unknown_feature(self) -> None:
        with self.assertRaises(KeyError):
            self.service.mark_feature_ready(
                "october-batch",
                "missing",
                readiness(),
            )

    def test_freeze_requires_exact_approval(self) -> None:
        self.register_and_ready()
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)

        with self.assertRaises(ValueError):
            self.service.freeze(
                "october-batch",
                {"manifest_sha256": "b" * 64},
                "Keep collecting",
            )

        frozen = self.service.freeze(
            "october-batch",
            {"manifest_sha256": "b" * 64},
            "Freeze and build",
        )
        self.assertEqual(CampaignState.FROZEN, frozen.state)

    def test_authorized_preflight_digest_is_consumed_once(self) -> None:
        self.service.transition("october-batch", CampaignState.IMPLEMENTING)
        self.service.transition("october-batch", CampaignState.PREFLIGHT)
        command = "ssh AndroidBuilder 'm SystemUI'"

        digest = self.service.authorize_preflight("october-batch", command)
        self.service.consume_preflight("october-batch", command)

        self.assertEqual(64, len(digest))
        with self.assertRaises(ValueError):
            self.service.consume_preflight("october-batch", command)

    def test_expensive_command_claims_one_build_attempt(self) -> None:
        self.advance_to_frozen()
        command = "ssh AndroidBuilder 'brunch salami'"

        claimed = self.service.claim_build("october-batch", command)

        self.assertEqual(CampaignState.BUILDING, claimed.state)
        self.assertEqual(1, len(claimed.build_attempts))
        self.assertNotIn(command, str(claimed.to_dict()))
        with self.assertRaises(ValueError):
            self.service.claim_build("october-batch", command)

    def test_device_lease_is_exclusive(self) -> None:
        self.advance_to_testing()

        leased = self.service.grant_device_lease("october-batch", "pulse")

        self.assertEqual("pulse", leased.active_device_lease)
        with self.assertRaises(ValueError):
            self.service.grant_device_lease("october-batch", "pulse")

    def test_failure_releases_lease_and_records_failure(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")

        campaign = self.service.record_device_result(
            "october-batch",
            "pulse",
            {
                "build_id": "20990101-000000",
                "passed": 1,
                "failed": 1,
                "blocked": 0,
                "not_run": 0,
                "failures": [
                    {
                        "feature_id": "pulse",
                        "case_id": "pulse-nav",
                        "observed": "not rendered",
                    }
                ],
                "restored_state": {"pulse_enabled": 0},
                "evidence": ["pulse-dump.txt"],
                "next_phase": "FIXING",
            },
        )

        self.assertIsNone(campaign.active_device_lease)
        self.assertEqual("pulse-nav", campaign.failures[0]["case_id"])
        self.assertEqual(FeaturePhase.FIXING, campaign.features["pulse"].phase)

    def test_concurrent_registrations_do_not_lose_updates(self) -> None:
        start = multiprocessing.Event()
        workers = [
            multiprocessing.Process(
                target=register_worker,
                args=(str(self.store.root), "pulse", "pulse-session", start),
            ),
            multiprocessing.Process(
                target=register_worker,
                args=(str(self.store.root), "crt", "crt-session", start),
            ),
        ]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(0, worker.exitcode)

        stored = self.store.load("october-batch")
        self.assertEqual({"pulse", "crt"}, set(stored.features))

    def test_follow_up_requires_affected_owner_ready(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")
        self.service.record_device_result(
            "october-batch",
            "pulse",
            {
                "build_id": "20990101-000000",
                "passed": 0,
                "failed": 1,
                "blocked": 0,
                "not_run": 0,
                "failures": [
                    {
                        "feature_id": "pulse",
                        "case_id": "pulse-nav",
                        "observed": "not rendered",
                    }
                ],
                "restored_state": {"pulse_enabled": 0},
                "evidence": ["pulse-dump.txt"],
                "next_phase": "FIXING",
            },
        )
        self.service.transition("october-batch", CampaignState.FIX_BATCH_READY)

        with self.assertRaises(ValueError):
            self.service.prepare_follow_up(
                "october-batch",
                "Freeze fixes and rebuild",
            )

        self.service.mark_feature_ready(
            "october-batch",
            "pulse",
            readiness(),
        )
        campaign = self.service.prepare_follow_up(
            "october-batch",
            "Freeze fixes and rebuild",
        )
        self.assertEqual(CampaignState.IMPLEMENTING, campaign.state)


if __name__ == "__main__":
    unittest.main()

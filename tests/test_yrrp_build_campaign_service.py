from __future__ import annotations

import hashlib
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.model import CampaignState, FeaturePhase, digest_json
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


def command_ref(step_id: str) -> dict:
    return {
        "check_id": step_id,
        "command_sha256": "f" * 64,
        "location": "device",
    }


def device_case(feature_id: str) -> dict:
    return {
        "case_id": f"{feature_id}-case",
        "setup_checks": [],
        "action_id": f"exercise-{feature_id}",
        "expected": f"{feature_id} behaves as designed",
        "cleanup_checks": [command_ref(f"restore-{feature_id}")],
    }


def registration(feature_id: str = "pulse") -> dict:
    command = f"m {feature_id}"
    return {
        "feature_id": feature_id,
        "phase": "IMPLEMENTING",
        "spec": f"docs/{feature_id}-spec.md",
        "plan": f"docs/{feature_id}-plan.md",
        "repositories": [f"repo/{feature_id}"],
        "cheap_checks": [
            {
                "check_id": f"{feature_id}-module",
                "command_sha256": CampaignService.command_digest(command),
                "location": "AndroidBuilder",
            }
        ],
        "device_cases": [device_case(feature_id)],
    }


def readiness(feature_id: str = "pulse", revision: str = "a") -> dict:
    repository = f"repo/{feature_id}"
    return {
        "revisions": {repository: revision * 40},
        "clean_repositories": [repository],
        "check_evidence": [f"evidence/{feature_id}-preflight.txt"],
        "observability": "ready",
        "restoration_steps": [command_ref(f"restore-{feature_id}")],
    }


MANIFEST = b"<manifest/>\n"


def snapshot(
    repositories: dict[str, str] | None = None,
    campaign_id: str = "october-batch",
) -> dict:
    revisions = repositories or {"repo/pulse": "a" * 40}
    feature_ids = [name.rsplit("/", 1)[-1] for name in revisions]
    concern_inventory = {
        feature_id: {
            "repositories": [repository],
            "revisions": {repository: revisions[repository]},
        }
        for feature_id, repository in zip(feature_ids, revisions, strict=True)
    }
    matrix = {
        feature_id: [device_case(feature_id)]
        for feature_id in feature_ids
    }
    return {
        "manifest_sha256": hashlib.sha256(MANIFEST).hexdigest(),
        "manifest_evidence": f"evidence/{campaign_id}/manifest.xml",
        "repositories": revisions,
        "branches": {name: "lineage-23.2" for name in revisions},
        "clean_repositories": sorted(revisions),
        "project_sha": "e" * 40,
        "build_mode": "signed-ota",
        "matrix_sha256": digest_json(matrix),
        "concern_inventory_sha256": digest_json(concern_inventory),
    }


def build_result(status: str = "complete") -> dict:
    return {
        "build_id": "20990101-000000",
        "status": status,
        "status_file": "/home/android/signed-build.status",
        "log_path": "/opt/android/out/signed/build.log",
        "project_sha": "e" * 40,
        "started_at": "2099-01-01T00:00:00Z",
        "completed_at": "2099-01-01T01:00:00Z",
        "final_status": "complete" if status == "complete" else "signing-failed:1",
        "signed_artifact_evidence": ["sha256.txt"],
        "public_ota_evidence": ["ota-health.txt"] if status == "complete" else [],
    }


def installation(source_snapshot: dict | None = None) -> dict:
    return {
        "build_id": "20990101-000000",
        "installed_at": "2099-01-01T01:15:00Z",
        "source_snapshot_sha256": digest_json(source_snapshot or snapshot()),
        "baseline_evidence": ["baseline-dumpsys.txt"],
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
        self.store.write_evidence(
            "october-batch",
            "manifest.xml",
            MANIFEST,
        )

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
            snapshot(),
            "Freeze and build",
            actor="yrrp-build-campaign",
        )

    def advance_to_testing(self) -> None:
        self.advance_to_frozen()
        self.service.claim_build(
            "october-batch",
            current_snapshot=snapshot(),
            actor="yrrp-build-campaign",
        )
        self.service.record_build_result("october-batch", build_result())
        self.service.record_installation("october-batch", installation())

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
                snapshot(),
                "Keep collecting",
                actor="yrrp-build-campaign",
            )

        frozen = self.service.freeze(
            "october-batch",
            snapshot(),
            "Freeze and build",
            actor="yrrp-build-campaign",
        )
        self.assertEqual(CampaignState.FROZEN, frozen.state)

    def test_preflight_rejects_product_build_commands(self) -> None:
        self.service.transition("october-batch", CampaignState.IMPLEMENTING)
        self.service.transition("october-batch", CampaignState.PREFLIGHT)

        for command in (
            "ssh AndroidBuilder 'mka dist'",
            "ssh AndroidBuilder 'brunch sala'\"'\"'mi'",
            "ssh AndroidBuilder '/opt/yrrp/project/scripts/sign-lineage-build.sh'",
            "ssh AndroidBuilder 'x=brunch; $x salami'",
            "ssh AndroidBuilder 'x=mka; $x bacon'",
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    self.service.authorize_preflight("october-batch", command)

    def test_authorized_preflight_digest_is_consumed_once(self) -> None:
        command = "ssh AndroidBuilder 'm SystemUI'"
        payload = registration()
        payload["cheap_checks"] = [
            {
                "check_id": "systemui-module",
                "command_sha256": self.service.command_digest(command),
                "location": "AndroidBuilder",
            }
        ]
        self.service.register_feature(
            "october-batch",
            payload,
            sender="feature-session",
        )
        self.service.transition("october-batch", CampaignState.IMPLEMENTING)
        self.service.transition("october-batch", CampaignState.PREFLIGHT)

        digest = self.service.authorize_preflight("october-batch", command)
        self.service.consume_preflight("october-batch", command)

        self.assertEqual(64, len(digest))
        with self.assertRaises(ValueError):
            self.service.consume_preflight("october-batch", command)

    def test_expensive_command_claims_one_build_attempt(self) -> None:
        self.advance_to_frozen()
        claimed = self.service.claim_build(
            "october-batch",
            current_snapshot=snapshot(),
            actor="yrrp-build-campaign",
        )

        self.assertEqual(CampaignState.BUILDING, claimed.state)
        self.assertEqual(1, len(claimed.build_attempts))
        self.assertEqual("yrrp-build-campaign", claimed.build_attempts[0]["actor"])
        with self.assertRaises(ValueError):
            self.service.claim_build(
                "october-batch",
                current_snapshot=snapshot(),
                actor="yrrp-build-campaign",
            )

    def test_device_lease_is_exclusive(self) -> None:
        self.advance_to_testing()

        leased = self.service.grant_device_lease("october-batch", "pulse")

        self.assertEqual("pulse", leased.active_device_lease)
        with self.assertRaises(ValueError):
            self.service.grant_device_lease("october-batch", "pulse")

    def test_failure_releases_lease_and_records_failure(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")
        self.service.record_case(
            "october-batch",
            {"case_id": "pulse-case", "feature_id": "pulse", "result": "FAIL"},
        )

        campaign = self.service.record_device_result(
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
        self.service.record_case(
            "october-batch",
            {"case_id": "pulse-case", "feature_id": "pulse", "result": "FAIL"},
        )
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
        self.service.finalize_testing("october-batch")

        with self.assertRaises(ValueError):
            self.service.prepare_follow_up("october-batch")

        self.service.mark_feature_ready(
            "october-batch",
            "pulse",
            readiness(),
        )
        campaign = self.service.prepare_follow_up("october-batch")
        self.assertEqual(CampaignState.IMPLEMENTING, campaign.state)

    def test_build_failure_can_begin_follow_up_without_feature_failure(self) -> None:
        self.advance_to_frozen()
        self.service.claim_build(
            "october-batch",
            current_snapshot=snapshot(),
            actor="yrrp-build-campaign",
        )
        self.service.record_build_result("october-batch", build_result("failed"))

        campaign = self.service.prepare_follow_up("october-batch")

        self.assertEqual(CampaignState.IMPLEMENTING, campaign.state)
        self.assertIsNone(campaign.source_snapshot)

    def test_follow_up_freeze_preserves_accepted_unaffected_feature(self) -> None:
        service = self.service
        service.create("follow-up")
        self.store.write_evidence("follow-up", "manifest.xml", MANIFEST)
        for feature_id, sender in (("pulse", "pulse-session"), ("crt", "crt-session")):
            service.register_feature(
                "follow-up",
                registration(feature_id),
                sender=sender,
            )
            service.mark_feature_ready(
                "follow-up",
                feature_id,
                readiness(feature_id, "a" if feature_id == "pulse" else "d"),
            )
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            service.transition("follow-up", state)
        first_snapshot = snapshot(
            {"repo/pulse": "a" * 40, "repo/crt": "d" * 40},
            campaign_id="follow-up",
        )
        service.freeze(
            "follow-up",
            first_snapshot,
            "Freeze and build",
            actor="yrrp-build-campaign",
        )
        service.claim_build(
            "follow-up",
            current_snapshot=first_snapshot,
            actor="yrrp-build-campaign",
        )
        service.record_build_result("follow-up", build_result())
        service.record_installation("follow-up", installation(first_snapshot))
        for feature_id, result in (("pulse", "PASS"), ("crt", "FAIL")):
            service.grant_device_lease("follow-up", feature_id)
            service.record_case(
                "follow-up",
                {
                    "case_id": f"{feature_id}-case",
                    "feature_id": feature_id,
                    "result": result,
                },
            )
            failed = int(result == "FAIL")
            service.record_device_result(
                "follow-up",
                feature_id,
                {
                    "build_id": "20990101-000000",
                    "passed": 1 - failed,
                    "failed": failed,
                    "blocked": 0,
                    "not_run": 0,
                    "failures": (
                        [
                            {
                                "feature_id": feature_id,
                                "case_id": f"{feature_id}-case",
                                "observed": "feature behavior failed",
                            }
                        ]
                        if failed
                        else []
                    ),
                    "restored_state": {f"{feature_id}_enabled": 0},
                    "evidence": [f"{feature_id}.txt"],
                    "next_phase": "FIXING" if failed else "ACCEPTED",
                },
            )
        service.finalize_testing("follow-up")
        service.mark_feature_ready("follow-up", "crt", readiness("crt", "f"))
        service.prepare_follow_up("follow-up")
        service.transition("follow-up", CampaignState.PREFLIGHT)
        service.transition("follow-up", CampaignState.READY_TO_FREEZE)
        follow_up_snapshot = snapshot(
            {"repo/pulse": "a" * 40, "repo/crt": "f" * 40},
            campaign_id="follow-up",
        )

        frozen = service.freeze(
            "follow-up",
            follow_up_snapshot,
            "Freeze fixes and rebuild",
            actor="yrrp-build-campaign",
        )

        self.assertEqual(CampaignState.FROZEN, frozen.state)
        self.assertEqual(FeaturePhase.ACCEPTED, frozen.features["pulse"].phase)
        self.assertEqual(FeaturePhase.READY_FOR_BUILD, frozen.features["crt"].phase)

    def test_generic_transition_rejects_guarded_states(self) -> None:
        self.register_and_ready()
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)

        for guarded in (
            CampaignState.FROZEN,
            CampaignState.BUILDING,
            CampaignState.BUILT,
            CampaignState.TESTING,
            CampaignState.FIX_BATCH_READY,
            CampaignState.ACCEPTED,
        ):
            with self.subTest(guarded=guarded):
                with self.assertRaises(ValueError):
                    self.service.transition("october-batch", guarded)

    def test_create_rejects_existing_campaign(self) -> None:
        self.service.register_feature(
            "october-batch",
            registration(),
            sender="feature-session",
        )

        with self.assertRaises(FileExistsError):
            self.service.create("october-batch")

        stored = self.store.load("october-batch")
        self.assertIn("pulse", stored.features)

    def test_freeze_requires_complete_matching_snapshot(self) -> None:
        self.register_and_ready()
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)
        incomplete = {"manifest_sha256": "b" * 64}
        mismatch = snapshot({"repo/pulse": "9" * 40})
        wrong_matrix = snapshot()
        wrong_matrix["matrix_sha256"] = "0" * 64

        with self.assertRaises(ValueError):
            self.service.freeze(
                "october-batch",
                incomplete,
                "Freeze and build",
                actor="yrrp-build-campaign",
            )
        with self.assertRaises(ValueError):
            self.service.freeze(
                "october-batch",
                mismatch,
                "Freeze and build",
                actor="yrrp-build-campaign",
            )
        with self.assertRaises(ValueError):
            self.service.freeze(
                "october-batch",
                wrong_matrix,
                "Freeze and build",
                actor="yrrp-build-campaign",
            )

    def test_prepare_snapshot_derives_campaign_hashes(self) -> None:
        self.register_and_ready()
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("october-batch", state)
        live_source = {
            key: value
            for key, value in snapshot().items()
            if key
            in {
                "manifest_sha256",
                "manifest_evidence",
                "repositories",
                "branches",
                "clean_repositories",
                "project_sha",
            }
        }

        prepared = self.service.prepare_snapshot("october-batch", live_source)

        self.assertEqual(snapshot(), prepared)

    def test_new_freeze_rejects_other_active_build_campaign(self) -> None:
        self.advance_to_frozen()
        self.service.claim_build(
            "october-batch",
            current_snapshot=snapshot(),
            actor="yrrp-build-campaign",
        )
        self.service.create("second-batch")
        self.store.write_evidence("second-batch", "manifest.xml", MANIFEST)
        self.service.register_feature(
            "second-batch",
            registration("crt"),
            sender="crt-session",
        )
        self.service.mark_feature_ready(
            "second-batch",
            "crt",
            readiness("crt", "d"),
        )
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            self.service.transition("second-batch", state)

        with self.assertRaises(ValueError):
            self.service.freeze(
                "second-batch",
                snapshot(
                    {"repo/crt": "d" * 40},
                    campaign_id="second-batch",
                ),
                "Freeze and build",
                actor="yrrp-build-campaign",
            )

    def test_frozen_campaign_rejects_feature_mutation(self) -> None:
        self.advance_to_frozen()

        with self.assertRaises(ValueError):
            self.service.register_feature(
                "october-batch",
                registration("crt"),
                sender="crt-session",
            )
        with self.assertRaises(ValueError):
            self.service.mark_feature_ready(
                "october-batch",
                "pulse",
                readiness("pulse", "f"),
            )
        with self.assertRaises(ValueError):
            self.service.set_feature_phase(
                "october-batch",
                "pulse",
                FeaturePhase.FIXING,
            )

    def test_set_feature_phase_rejects_guarded_phases(self) -> None:
        self.service.register_feature(
            "october-batch",
            registration(),
            sender="feature-session",
        )

        for phase in (
            FeaturePhase.READY_FOR_BUILD,
            FeaturePhase.AWAITING_DEVICE_LEASE,
            FeaturePhase.DEVICE_TESTING,
            FeaturePhase.ACCEPTED,
        ):
            with self.subTest(phase=phase):
                with self.assertRaises(ValueError):
                    self.service.set_feature_phase(
                        "october-batch",
                        "pulse",
                        phase,
                    )

    def test_build_claim_rejects_stale_source_snapshot(self) -> None:
        self.advance_to_frozen()
        stale = snapshot({"repo/pulse": "9" * 40})

        with self.assertRaises(ValueError):
            self.service.claim_build(
                "october-batch",
                current_snapshot=stale,
                actor="yrrp-build-campaign",
            )

    def test_device_result_requires_every_assigned_case(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")

        with self.assertRaises(ValueError):
            self.service.record_device_result(
                "october-batch",
                "pulse",
                {
                    "build_id": "20990101-000000",
                    "passed": 0,
                    "failed": 0,
                    "blocked": 0,
                    "not_run": 0,
                    "failures": [],
                    "restored_state": {"pulse_enabled": 0},
                    "evidence": ["pulse.txt"],
                    "next_phase": "ACCEPTED",
                },
            )

    def test_record_case_rejects_unassigned_case(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")

        with self.assertRaises(ValueError):
            self.service.record_case(
                "october-batch",
                {
                    "case_id": "invented-case",
                    "feature_id": "pulse",
                    "result": "PASS",
                },
            )

    def test_device_result_validates_counts_build_and_phase(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")
        invalid_results = (
            {
                "build_id": "wrong",
                "passed": 1,
                "failed": 0,
                "blocked": 0,
                "not_run": 0,
                "failures": [],
                "restored_state": {},
                "evidence": ["pulse.txt"],
                "next_phase": "ACCEPTED",
            },
            {
                "build_id": "20990101-000000",
                "passed": -1,
                "failed": 0,
                "blocked": 0,
                "not_run": 0,
                "failures": [],
                "restored_state": {},
                "evidence": ["pulse.txt"],
                "next_phase": "ACCEPTED",
            },
            {
                "build_id": "20990101-000000",
                "passed": 0,
                "failed": 1,
                "blocked": 0,
                "not_run": 0,
                "failures": [],
                "restored_state": {},
                "evidence": ["pulse.txt"],
                "next_phase": "READY_FOR_BUILD",
            },
        )

        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    self.service.record_device_result(
                        "october-batch",
                        "pulse",
                        result,
                    )

    def test_acceptance_requires_complete_matrix_and_restoration(self) -> None:
        self.advance_to_testing()
        self.service.grant_device_lease("october-batch", "pulse")
        self.service.record_case(
            "october-batch",
            {"case_id": "pulse-case", "feature_id": "pulse", "result": "PASS"},
        )
        self.service.record_device_result(
            "october-batch",
            "pulse",
            {
                "build_id": "20990101-000000",
                "passed": 1,
                "failed": 0,
                "blocked": 0,
                "not_run": 0,
                "failures": [],
                "restored_state": {"pulse_enabled": 0},
                "evidence": ["pulse.txt"],
                "next_phase": "ACCEPTED",
            },
        )

        accepted = self.service.finalize_testing("october-batch")

        self.assertEqual(CampaignState.ACCEPTED, accepted.state)
        self.assertEqual(FeaturePhase.ACCEPTED, accepted.features["pulse"].phase)

    def test_two_features_share_one_build_and_serial_device_leases(self) -> None:
        service = self.service
        store = self.store
        service.create("dry-run")
        store.write_evidence("dry-run", "manifest.xml", MANIFEST)
        commands = {
            "pulse": "ssh AndroidBuilder 'm SystemUI'",
            "crt": "ssh AndroidBuilder 'atest SystemUiRavenTests'",
        }
        for feature_id in ("pulse", "crt"):
            payload = registration(feature_id)
            payload["cheap_checks"] = [
                {
                    "check_id": f"{feature_id}-preflight",
                    "command_sha256": service.command_digest(commands[feature_id]),
                    "location": "AndroidBuilder",
                }
            ]
            service.register_feature(
                "dry-run",
                payload,
                sender=f"{feature_id}-session",
            )
        service.transition("dry-run", CampaignState.IMPLEMENTING)
        service.transition("dry-run", CampaignState.PREFLIGHT)
        for command in commands.values():
            service.authorize_preflight("dry-run", command)
            service.consume_preflight("dry-run", command)
        service.mark_feature_ready("dry-run", "pulse", readiness("pulse", "a"))
        service.mark_feature_ready("dry-run", "crt", readiness("crt", "d"))
        service.transition("dry-run", CampaignState.READY_TO_FREEZE)
        frozen_snapshot = snapshot(
            {"repo/pulse": "a" * 40, "repo/crt": "d" * 40},
            campaign_id="dry-run",
        )
        service.freeze(
            "dry-run",
            frozen_snapshot,
            "Freeze and build",
            actor="yrrp-build-campaign",
        )
        service.claim_build(
            "dry-run",
            current_snapshot=frozen_snapshot,
            actor="yrrp-build-campaign",
        )
        service.record_build_result("dry-run", build_result())
        service.record_installation("dry-run", installation(frozen_snapshot))
        service.grant_device_lease("dry-run", "pulse")
        with self.assertRaises(ValueError):
            service.grant_device_lease("dry-run", "crt")
        service.record_case(
            "dry-run",
            {"case_id": "pulse-case", "feature_id": "pulse", "result": "PASS"},
        )
        service.record_device_result(
            "dry-run",
            "pulse",
            {
                "build_id": "20990101-000000",
                "passed": 1,
                "failed": 0,
                "blocked": 0,
                "not_run": 0,
                "failures": [],
                "restored_state": {"pulse_enabled": 0},
                "evidence": ["pulse.txt"],
                "next_phase": "ACCEPTED",
            },
        )
        service.grant_device_lease("dry-run", "crt")
        service.record_case(
            "dry-run",
            {"case_id": "crt-case", "feature_id": "crt", "result": "FAIL"},
        )
        service.record_device_result(
            "dry-run",
            "crt",
            {
                "build_id": "20990101-000000",
                "passed": 0,
                "failed": 1,
                "blocked": 0,
                "not_run": 0,
                "failures": [
                    {
                        "feature_id": "crt",
                        "case_id": "crt-case",
                        "observed": "animation absent",
                    }
                ],
                "restored_state": {"crt_enabled": 1},
                "evidence": ["crt.txt"],
                "next_phase": "FIXING",
            },
        )
        service.finalize_testing("dry-run")

        state_text = store.json_path("dry-run").read_text()
        ledger = store.markdown_path("dry-run").read_text()
        self.assertIn("pulse-session", ledger)
        self.assertIn("crt-session", ledger)
        self.assertIn('"result": "PASS"', ledger)
        self.assertIn('"result": "FAIL"', ledger)
        self.assertNotIn("brunch salami", state_text)
        self.assertIn("yrrp-build-campaign", state_text)


if __name__ == "__main__":
    unittest.main()

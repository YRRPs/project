from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def command_ref(step_id: str) -> dict:
    return {
        "check_id": step_id,
        "command_sha256": "c" * 64,
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


class CampaignModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = importlib.import_module("yrrp_build_campaign.model")

    def test_slug_accepts_bounded_identifiers(self) -> None:
        self.assertEqual("pulse-crt_1.0", self.model.validate_slug("pulse-crt_1.0"))

    def test_slug_rejects_traversal_and_absolute_paths(self) -> None:
        for value in ("../pulse", "/pulse", "pulse/child", "", "A" * 65):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.model.validate_slug(value)

    def test_registration_rejects_empty_acceptance_matrix(self) -> None:
        with self.assertRaises(ValueError):
            self.model.FeatureRecord.from_registration(
                {
                    "feature_id": "pulse",
                    "phase": "IMPLEMENTING",
                    "spec": "docs/spec.md",
                    "plan": "docs/plan.md",
                    "repositories": ["frameworks/base"],
                    "cheap_checks": [
                        {
                            "check_id": "systemui-module",
                            "command_sha256": "c" * 64,
                            "location": "AndroidBuilder",
                        }
                    ],
                    "device_cases": [],
                },
                sender="feature-session",
            )

    def test_registration_rejects_raw_device_commands(self) -> None:
        with self.assertRaises(ValueError):
            self.model.FeatureRecord.from_registration(
                {
                    "feature_id": "pulse",
                    "phase": "IMPLEMENTING",
                    "spec": "docs/spec.md",
                    "plan": "docs/plan.md",
                    "repositories": ["frameworks/base"],
                    "cheap_checks": [
                        {
                            "check_id": "systemui-module",
                            "command_sha256": "c" * 64,
                            "location": "AndroidBuilder",
                        }
                    ],
                    "device_cases": [
                        {
                            **device_case(),
                            "expected": "adb shell settings get secure x",
                        }
                    ],
                },
                sender="feature-session",
            )

    def test_registration_rejects_repository_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self.model.FeatureRecord.from_registration(
                {
                    "feature_id": "pulse",
                    "phase": "IMPLEMENTING",
                    "spec": "docs/spec.md",
                    "plan": "docs/plan.md",
                    "repositories": ["../outside"],
                    "cheap_checks": [
                        {
                            "check_id": "systemui-module",
                            "command_sha256": "c" * 64,
                            "location": "AndroidBuilder",
                        }
                    ],
                    "device_cases": [device_case()],
                },
                sender="feature-session",
            )

    def test_campaign_transition_graph_rejects_skips(self) -> None:
        campaign = self.model.Campaign.new("october-batch")

        with self.assertRaises(ValueError):
            campaign.transition(self.model.CampaignState.FROZEN)

    def test_feature_must_register_before_ready(self) -> None:
        feature = self.model.FeatureRecord.from_registration(
            {
                "feature_id": "pulse",
                "phase": "UNREGISTERED",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": [
                    {
                        "check_id": "systemui-module",
                        "command_sha256": "c" * 64,
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
            },
            sender="",
        )

        with self.assertRaises(ValueError):
            feature.mark_ready(
                {
                    "revisions": {"frameworks/base": "a" * 40},
                    "clean_repositories": ["frameworks/base"],
                    "check_evidence": ["evidence/preflight.txt"],
                    "observability": "ready",
                    "restoration_steps": [command_ref("restore-pulse")],
                }
            )

    def test_readiness_rejects_raw_command_fields(self) -> None:
        feature = self.model.FeatureRecord.from_registration(
            {
                "feature_id": "pulse",
                "phase": "IMPLEMENTING",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": [
                    {
                        "check_id": "systemui-module",
                        "command_sha256": "c" * 64,
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
            },
            sender="feature-session",
        )
        readiness = {
            "revisions": {"frameworks/base": "a" * 40},
            "clean_repositories": ["frameworks/base"],
            "check_evidence": ["preflight.txt"],
            "observability": "ready",
            "restoration_steps": [command_ref("restore-pulse")],
            "command": "raw command text",
        }

        with self.assertRaises(ValueError):
            feature.mark_ready(readiness)

    def test_readiness_rejects_raw_restoration_commands(self) -> None:
        feature = self.model.FeatureRecord.from_registration(
            {
                "feature_id": "pulse",
                "phase": "IMPLEMENTING",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": [command_ref("systemui-module")],
                "device_cases": [device_case()],
            },
            sender="feature-session",
        )
        readiness = {
            "revisions": {"frameworks/base": "a" * 40},
            "clean_repositories": ["frameworks/base"],
            "check_evidence": ["preflight.txt"],
            "observability": "ready",
            "restoration_steps": ["adb shell settings put secure x 0"],
        }

        with self.assertRaises(ValueError):
            feature.mark_ready(readiness)

    def test_frozen_campaign_requires_every_feature_ready(self) -> None:
        campaign = self.model.Campaign.new("october-batch")
        campaign.state = self.model.CampaignState.READY_TO_FREEZE
        campaign.features["pulse"] = self.model.FeatureRecord.from_registration(
            {
                "feature_id": "pulse",
                "phase": "IMPLEMENTING",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": [
                    {
                        "check_id": "systemui-module",
                        "command_sha256": "c" * 64,
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
            },
            sender="feature-session",
        )

        with self.assertRaises(ValueError):
            campaign.freeze(
                {"manifest_sha256": "b" * 64},
                "Freeze and build",
                "orchestrator-session",
            )


if __name__ == "__main__":
    unittest.main()

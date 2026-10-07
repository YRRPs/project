from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOOK = ROOT / ".claude/hooks/yrrp-build-campaign-gate.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.model import CampaignState
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


def load_hook():
    spec = importlib.util.spec_from_file_location("yrrp_campaign_gate", HOOK)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load campaign gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(command: str, agent_type: str = "yrrp-build-campaign") -> dict:
    return {
        "session_id": "session-test",
        "cwd": str(ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": command,
            "description": "fixture",
            "timeout": 120000,
            "run_in_background": False,
        },
        "tool_use_id": "tool-test",
        "agent_type": agent_type,
    }


class CampaignGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "campaigns"
        self.previous = os.environ.get("YRRP_CAMPAIGN_ROOT")
        os.environ["YRRP_CAMPAIGN_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("YRRP_CAMPAIGN_ROOT", None)
        else:
            os.environ["YRRP_CAMPAIGN_ROOT"] = self.previous
        self.temp.cleanup()

    def service(self) -> CampaignService:
        return CampaignService(CampaignStore(self.root))

    def prepare_frozen(self) -> None:
        service = self.service()
        service.create("october-batch")
        service.register_feature(
            "october-batch",
            {
                "feature_id": "pulse",
                "phase": "IMPLEMENTING",
                "spec": "docs/spec.md",
                "plan": "docs/plan.md",
                "repositories": ["frameworks/base"],
                "cheap_checks": ["m SystemUI"],
                "device_cases": [{"case_id": "pulse-nav"}],
            },
            sender="feature-session",
        )
        service.mark_feature_ready(
            "october-batch",
            "pulse",
            {
                "revisions": {"frameworks/base": "a" * 40},
                "clean_repositories": ["frameworks/base"],
                "check_evidence": ["evidence/preflight.txt"],
                "observability": "ready",
            },
        )
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            service.transition("october-batch", state)
        service.freeze(
            "october-batch",
            {"manifest_sha256": "b" * 64},
            "Freeze and build",
        )

    def test_classifies_every_expensive_form(self) -> None:
        hook = load_hook()
        commands = (
            "ssh AndroidBuilder '/opt/yrrp/project/scripts/sign-lineage-build.sh'",
            "ssh AndroidBuilder 'brunch salami'",
            "ssh AndroidBuilder 'mka target-files-package otatools'",
            "ssh AndroidBuilder \"screen -dmS build bash -lc 'brunch salami'\"",
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual("expensive", hook.classify(command))

    def test_read_only_builder_command_has_no_opinion(self) -> None:
        hook = load_hook()

        self.assertIsNone(
            hook.evaluate(event("ssh AndroidBuilder 'cat build.status'"))
        )

    def test_direct_build_is_denied_without_campaign(self) -> None:
        hook = load_hook()

        output = hook.evaluate(event("ssh AndroidBuilder 'brunch salami'"))

        self.assertEqual(
            "deny",
            output["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertIn(
            "campaign",
            output["hookSpecificOutput"]["permissionDecisionReason"].lower(),
        )

    def test_shell_metacharacters_are_inert_data(self) -> None:
        hook = load_hook()
        marker = Path(self.temp.name) / "executed"
        command = f"ssh AndroidBuilder 'brunch salami'; touch {marker}"

        hook.evaluate(event(command))

        self.assertFalse(marker.exists())

    def test_frozen_campaign_claims_exactly_one_build(self) -> None:
        self.prepare_frozen()
        hook = load_hook()
        command = "ssh AndroidBuilder 'brunch salami'"

        first = hook.evaluate(event(command))
        second = hook.evaluate(event(command))

        self.assertEqual(
            "allow",
            first["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertEqual(
            "deny",
            second["hookSpecificOutput"]["permissionDecision"],
        )
        campaign = CampaignStore(self.root).load("october-batch")
        self.assertEqual(CampaignState.BUILDING, campaign.state)
        self.assertEqual(1, len(campaign.build_attempts))
        self.assertNotIn(command, str(campaign.to_dict()))

    def test_preflight_requires_one_time_authorization(self) -> None:
        service = self.service()
        service.create("october-batch")
        service.transition("october-batch", CampaignState.IMPLEMENTING)
        service.transition("october-batch", CampaignState.PREFLIGHT)
        command = "ssh AndroidBuilder 'm SystemUI'"
        service.authorize_preflight("october-batch", command)
        hook = load_hook()

        first = hook.evaluate(event(command))
        second = hook.evaluate(event(command))

        self.assertEqual(
            "allow",
            first["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertEqual(
            "deny",
            second["hookSpecificOutput"]["permissionDecision"],
        )

    def test_process_fails_closed_for_malformed_json(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input="{",
            capture_output=True,
            text=True,
            env=os.environ | {"YRRP_CAMPAIGN_ROOT": str(self.root)},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(
            "deny",
            output["hookSpecificOutput"]["permissionDecision"],
        )

    def test_process_is_silent_for_read_only_command(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(event("ssh AndroidBuilder 'cat build.status'")),
            capture_output=True,
            text=True,
            env=os.environ | {"YRRP_CAMPAIGN_ROOT": str(self.root)},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()

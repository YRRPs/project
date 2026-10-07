from __future__ import annotations

import hashlib
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


def restoration_steps() -> list[dict]:
    return [
        {
            "check_id": "restore-pulse",
            "command_sha256": "f" * 64,
            "location": "device",
        }
    ]


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


GENERATE_RECOVERY = (
    "ssh AndroidBuilder /opt/yrrp/project/scripts/generate-incremental-ota.sh "
    "--source-build 20261007-083337 --target-build 20261008-090000"
)
DEPLOY_RECOVERY = (
    "ssh AndroidBuilder /opt/yrrp/project/scripts/deploy-ota-release.sh "
    "--ota /opt/android/out/signed/lineage-23.2-salami-20261008-090000-signed-ota.zip "
    "--target-files /opt/android/out/signed/lineage-23.2-salami-20261008-090000-signed-target_files.zip "
    "--build-id 20261008-090000 "
    "--incremental /opt/android/out/signed/"
    "lineage-23.2-salami-20261007-083337-to-20261008-090000-signed-incremental-ota.zip"
)


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

    def prepare_frozen(self, pending_recovery: str | None = None) -> None:
        service = self.service()
        service.create("october-batch")
        manifest = b"<manifest/>\n"
        service.store.write_evidence("october-batch", "manifest.xml", manifest)
        service.register_feature(
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
                        "command_sha256": service.command_digest("m SystemUI"),
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
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
                "restoration_steps": restoration_steps(),
            },
        )
        for state in (
            CampaignState.IMPLEMENTING,
            CampaignState.PREFLIGHT,
            CampaignState.READY_TO_FREEZE,
        ):
            service.transition("october-batch", state)
        prepared = service.prepare_snapshot(
            "october-batch",
            {
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "manifest_evidence": "evidence/october-batch/manifest.xml",
                "repositories": {"frameworks/base": "a" * 40},
                "branches": {"frameworks/base": "lineage-23.2"},
                "clean_repositories": ["frameworks/base"],
                "project_sha": "e" * 40,
            },
        )
        if pending_recovery is not None:
            service.authorize_recovery("october-batch", pending_recovery)
        service.freeze(
            "october-batch",
            prepared,
            "Freeze and build",
            actor="yrrp-build-campaign",
        )

    def test_classifies_exact_launcher_and_raw_builder_commands(self) -> None:
        hook = load_hook()
        launcher = (
            "python3 scripts/yrrp-launch-campaign-build.py "
            "--campaign-id october-batch"
        )
        self.assertEqual("launcher", hook.classify(launcher))
        raw_commands = (
            "ssh AndroidBuilder '/opt/yrrp/project/scripts/sign-lineage-build.sh'",
            "ssh AndroidBuilder 'brunch sala'\"'\"'mi'",
            "ssh AndroidBuilder 'mka dist'",
            "ssh -p 4242 AndroidBuilder 'mka target-files-package otatools'",
            "ssh Android\"\"Builder 'mka dist'",
            "sign-lineage-build.sh; echo done",
            "ssh AndroidBuilder 'cat status; brunch salami'",
            "ssh AndroidBuilder 'git status; mka bacon'",
        )
        for command in raw_commands:
            with self.subTest(command=command):
                self.assertEqual("builder", hook.classify(command))

    def test_read_only_text_search_is_not_a_build(self) -> None:
        hook = load_hook()

        self.assertEqual("other", hook.classify("grep -R 'brunch salami' ."))
        self.assertEqual(
            "other",
            hook.classify("git add scripts/sign-lineage-build.sh"),
        )

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

    def test_frozen_campaign_allows_only_orchestrator_launcher(self) -> None:
        self.prepare_frozen()
        hook = load_hook()
        launcher = (
            "python3 scripts/yrrp-launch-campaign-build.py "
            "--campaign-id october-batch"
        )

        allowed = hook.evaluate(event(launcher))
        wrong_actor = hook.evaluate(event(launcher, agent_type="yrrp-feature-owner"))
        raw_build = hook.evaluate(event("ssh AndroidBuilder 'brunch salami'"))

        self.assertEqual(
            "allow",
            allowed["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertEqual(
            "deny",
            wrong_actor["hookSpecificOutput"]["permissionDecision"],
        )
        self.assertEqual(
            "deny",
            raw_build["hookSpecificOutput"]["permissionDecision"],
        )
        campaign = CampaignStore(self.root).load("october-batch")
        self.assertEqual(CampaignState.FROZEN, campaign.state)
        self.assertEqual([], campaign.build_attempts)
        self.assertEqual(1, len(campaign.launcher_authorizations))

    def test_preflight_requires_one_time_authorization(self) -> None:
        service = self.service()
        service.create("october-batch")
        command = "ssh AndroidBuilder 'm SystemUI'"
        service.register_feature(
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
                        "command_sha256": service.command_digest(command),
                        "location": "AndroidBuilder",
                    }
                ],
                "device_cases": [device_case()],
            },
            sender="feature-session",
        )
        service.transition("october-batch", CampaignState.IMPLEMENTING)
        service.transition("october-batch", CampaignState.PREFLIGHT)
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

    def test_process_denies_unreadable_campaign_state(self) -> None:
        self.root.mkdir(parents=True)
        active = self.root / "active"
        active.write_text("october-batch\n")
        active.chmod(0)
        try:
            result = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps(
                    event(
                        "python3 scripts/yrrp-launch-campaign-build.py "
                        "--campaign-id october-batch"
                    )
                ),
                capture_output=True,
                text=True,
                env=os.environ | {"YRRP_CAMPAIGN_ROOT": str(self.root)},
            )
        finally:
            active.chmod(0o600)

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

    def test_recovery_command_shapes(self) -> None:
        from yrrp_build_campaign.command_policy import is_supported_recovery_command

        full_only = DEPLOY_RECOVERY.split(" --incremental ")[0]
        for accepted in (GENERATE_RECOVERY, DEPLOY_RECOVERY, full_only):
            self.assertTrue(is_supported_recovery_command(accepted), accepted)
        for rejected in (
            GENERATE_RECOVERY + " --extra x",
            GENERATE_RECOVERY.replace("20261007-083337", "latest"),
            DEPLOY_RECOVERY.replace("/opt/android/out/signed/lineage-23.2-salami-20261008-090000-signed-ota", "/tmp/x"),
            DEPLOY_RECOVERY.replace("-to-20261008-090000", "-to-20261009-090000"),
            full_only.replace("--build-id 20261008-090000", "--build-id 20261009-090000"),
            "ssh AndroidBuilder /opt/yrrp/project/scripts/sign-lineage-build.sh",
            GENERATE_RECOVERY + "; rm -rf /",
            "ssh AndroidBuilder m SystemUI",
            "bash -c 'ssh AndroidBuilder rm -rf /opt/android/out' ; " + GENERATE_RECOVERY,
            GENERATE_RECOVERY.replace(
                "ssh AndroidBuilder", "ssh -o 'ProxyCommand=sh -c \"x\"' AndroidBuilder"
            ),
            GENERATE_RECOVERY.replace(
                "ssh AndroidBuilder", "ssh -o SetEnv=YRRP_BUILD_LOCK_HELD=1 AndroidBuilder"
            ),
            GENERATE_RECOVERY.replace("20261008-090000", "20261007-083337"),
        ):
            self.assertFalse(is_supported_recovery_command(rejected), rejected)

    def test_recovery_authorization_is_one_time(self) -> None:
        service = self.service()
        service.create("october-batch")
        service.authorize_recovery("october-batch", GENERATE_RECOVERY)
        hook = load_hook()
        first = hook.evaluate(event(GENERATE_RECOVERY))
        second = hook.evaluate(event(GENERATE_RECOVERY))
        self.assertEqual("allow", first["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual("deny", second["hookSpecificOutput"]["permissionDecision"])

    def test_recovery_consumption_requires_campaign_agent(self) -> None:
        service = self.service()
        service.create("october-batch")
        service.authorize_recovery("october-batch", GENERATE_RECOVERY)
        hook = load_hook()

        foreign = hook.evaluate(event(GENERATE_RECOVERY, agent_type="yrrp-feature-owner"))
        missing = hook.evaluate(event(GENERATE_RECOVERY, agent_type=""))
        with self.assertRaisesRegex(ValueError, "yrrp-build-campaign"):
            service.consume_recovery("october-batch", GENERATE_RECOVERY, "yrrp-feature-owner")
        owner = hook.evaluate(event(GENERATE_RECOVERY))

        self.assertEqual("deny", foreign["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("recovery", foreign["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual("deny", missing["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual("allow", owner["hookSpecificOutput"]["permissionDecision"])
        campaign = service.store.load("october-batch")
        self.assertEqual([], campaign.recovery_authorizations)
        self.assertEqual("recovery-consumed", campaign.events[-1]["kind"])

    def test_recovery_and_preflight_authorizations_are_separate(self) -> None:
        service = self.service()
        service.create("october-batch")
        digest = service.authorize_recovery("october-batch", GENERATE_RECOVERY)

        campaign = service.store.load("october-batch")
        self.assertEqual([digest], campaign.recovery_authorizations)
        self.assertEqual([], campaign.preflight_authorizations)
        with self.assertRaisesRegex(ValueError, "preflight command is not authorized"):
            service.consume_preflight("october-batch", GENERATE_RECOVERY)

        service.store.mutate(
            "october-batch",
            lambda campaign: campaign.preflight_authorizations.append(
                service.command_digest(DEPLOY_RECOVERY)
            ),
        )
        with self.assertRaisesRegex(ValueError, "recovery command is not authorized"):
            service.consume_recovery("october-batch", DEPLOY_RECOVERY, "yrrp-build-campaign")
        denied = load_hook().evaluate(event(DEPLOY_RECOVERY))
        self.assertEqual("deny", denied["hookSpecificOutput"]["permissionDecision"])

    def test_authorize_recovery_cli_is_campaign_only(self) -> None:
        hook = load_hook()
        payload = "printf '%s' '{\"command\": \"x\"}' | "
        commands = (
            "python3 scripts/yrrp-build-campaign.py authorize-recovery --campaign-id october-batch",
            f"python3 {ROOT}/scripts/yrrp-build-campaign.py authorize-recovery "
            "--campaign-id october-batch",
            payload + "python3 scripts/yrrp-build-campaign.py authorize-recovery "
            "--campaign-id october-batch",
            "python3 ./scripts/yrrp-build-campaign.py authorize-recovery --campaign-id october-batch",
            "python3 scripts/yrrp-build-campaign.py authorize-recovery;true",
        )
        for command in commands:
            with self.subTest(command=command):
                denied = hook.evaluate(event(command, agent_type="yrrp-feature-owner"))
                self.assertEqual("deny", denied["hookSpecificOutput"]["permissionDecision"])
                self.assertIn(
                    "yrrp-build-campaign",
                    denied["hookSpecificOutput"]["permissionDecisionReason"],
                )
                self.assertEqual(
                    "deny",
                    hook.evaluate(event(command, agent_type=""))[
                        "hookSpecificOutput"
                    ]["permissionDecision"],
                )
                self.assertIsNone(hook.evaluate(event(command)))
        self.assertIsNone(
            hook.evaluate(
                event(
                    "python3 scripts/yrrp-build-campaign.py status --campaign-id october-batch",
                    agent_type="yrrp-feature-owner",
                )
            )
        )

    def test_authorize_recovery_cli_cannot_carry_a_builder_command(self) -> None:
        hook = load_hook()
        command = (
            "python3 scripts/yrrp-build-campaign.py authorize-recovery "
            "--campaign-id october-batch < payload.json; ssh AndroidBuilder cat status"
        )

        output = hook.evaluate(event(command))

        self.assertEqual("deny", output["hookSpecificOutput"]["permissionDecision"])

    def test_state_without_recovery_authorizations_loads(self) -> None:
        service = self.service()
        service.create("october-batch")
        path = service.store.json_path("october-batch")
        value = json.loads(path.read_text())
        value.pop("recovery_authorizations", None)
        path.write_text(json.dumps(value))

        campaign = service.store.load("october-batch")

        self.assertEqual([], campaign.recovery_authorizations)

    def test_pending_recovery_does_not_block_freeze(self) -> None:
        self.prepare_frozen(pending_recovery=GENERATE_RECOVERY)

        campaign = self.service().store.load("october-batch")

        self.assertEqual(CampaignState.FROZEN, campaign.state)

    def test_freeze_clears_pending_recovery_authorizations(self) -> None:
        self.prepare_frozen(pending_recovery=GENERATE_RECOVERY)

        campaign = self.service().store.load("october-batch")
        cleared = [
            item for item in campaign.events
            if item["kind"] == "recovery-authorizations-cleared"
        ]
        decision = load_hook().evaluate(event(GENERATE_RECOVERY))

        self.assertEqual([], campaign.recovery_authorizations)
        self.assertEqual(1, len(cleared))
        self.assertEqual(1, cleared[0]["details"]["count"])
        self.assertEqual("deny", decision["hookSpecificOutput"]["permissionDecision"])

    def test_freeze_without_recovery_authorizations_records_no_clear_event(self) -> None:
        self.prepare_frozen()

        campaign = self.service().store.load("october-batch")

        self.assertNotIn(
            "recovery-authorizations-cleared",
            [item["kind"] for item in campaign.events],
        )

    def test_recovery_consumption_denied_while_frozen(self) -> None:
        self.prepare_frozen()
        service = self.service()
        digest = service.command_digest(GENERATE_RECOVERY)

        def inject(loaded) -> None:
            loaded.recovery_authorizations.append(digest)

        service.store.mutate("october-batch", inject)
        with self.assertRaisesRegex(ValueError, "FROZEN; recovery is not allowed"):
            service.consume_recovery("october-batch", GENERATE_RECOVERY, "yrrp-build-campaign")
        decision = load_hook().evaluate(event(GENERATE_RECOVERY))

        self.assertEqual("deny", decision["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual(
            [digest], service.store.load("october-batch").recovery_authorizations
        )

    def test_recovery_authorization_rejects_other_commands(self) -> None:
        service = self.service()
        service.create("october-batch")
        with self.assertRaisesRegex(ValueError, "unsupported recovery command"):
            service.authorize_recovery("october-batch", "ssh AndroidBuilder m SystemUI")

    def test_recovery_authorization_refused_while_frozen(self) -> None:
        self.prepare_frozen()
        with self.assertRaisesRegex(ValueError, "FROZEN"):
            self.service().authorize_recovery("october-batch", GENERATE_RECOVERY)


if __name__ == "__main__":
    unittest.main()

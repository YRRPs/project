from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".claude/agents/yrrp-build-campaign.md"
FEATURE_OWNER = ROOT / ".claude/agents/yrrp-feature-owner.md"
SIGNED_RELEASE_SKILL = ROOT / ".claude/skills/yrrp-signed-ota-release/SKILL.md"


def split_agent(text: str) -> tuple[str, str]:
    first, frontmatter, body = text.split("---", 2)
    if first.strip():
        raise ValueError("agent file must start with frontmatter")
    return frontmatter, body


def description(frontmatter: str) -> str:
    for line in frontmatter.splitlines():
        if line.startswith("description: "):
            return line.removeprefix("description: ")
    raise ValueError("description is missing")


class AgentDefinitionTest(unittest.TestCase):
    def test_orchestrator_has_required_identity_and_tools(self) -> None:
        text = ORCHESTRATOR.read_text()
        frontmatter, body = split_agent(text)

        self.assertIn("name: yrrp-build-campaign", frontmatter)
        for tool in (
            "ListAgents",
            "SendMessage",
            "AskUserQuestion",
            "Bash",
            "Skill",
        ):
            self.assertIn(f"  - {tool}", frontmatter)
        for forbidden in (
            "  - Edit",
            "  - Write",
            "  - NotebookEdit",
            "  - Agent",
        ):
            self.assertNotIn(forbidden, frontmatter)
        self.assertIn("claude --agent yrrp-build-campaign", body)

    def test_feature_owner_is_broad_main_session_agent(self) -> None:
        text = FEATURE_OWNER.read_text()
        frontmatter, body = split_agent(text)

        self.assertIn("name: yrrp-feature-owner", frontmatter)
        self.assertIn("model: inherit", frontmatter)
        self.assertNotIn("tools:", frontmatter)
        self.assertIn("claude --agent yrrp-feature-owner", body)
        self.assertIn("Background-subagent execution is unsupported", body)

    def test_orchestrator_uses_guarded_build_and_acceptance_commands(self) -> None:
        body = ORCHESTRATOR.read_text()
        for value in (
            "yrrp-launch-campaign-build.py",
            "--actor yrrp-build-campaign",
            "record-installation",
            "finalize-testing",
            "--bare",
        ):
            self.assertIn(value, body)

    def test_feature_owner_registers_safe_check_metadata(self) -> None:
        body = FEATURE_OWNER.read_text()
        for value in ("check_id", "command_sha256", "location"):
            self.assertIn(value, body)

    def test_message_contracts_exist_in_both_roles(self) -> None:
        orchestrator = ORCHESTRATOR.read_text()
        feature = FEATURE_OWNER.read_text()

        for contract in (
            "REGISTER_FEATURE",
            "READY_FOR_BUILD",
            "DEVICE_LEASE_GRANTED",
            "FEATURE_VERIFICATION_RESULT",
        ):
            self.assertIn(contract, orchestrator)
            self.assertIn(contract, feature)

    def test_descriptions_are_trigger_lists_with_non_cases(self) -> None:
        for path, phrase in (
            (ORCHESTRATOR, "avoiding another expensive build"),
            (FEATURE_OWNER, "active user brainstorming"),
        ):
            with self.subTest(path=path):
                frontmatter, _ = split_agent(path.read_text())
                value = description(frontmatter)
                self.assertGreaterEqual(len(value), 120)
                self.assertIn(phrase, value)
                self.assertIn("NOT for", value)

    def test_agents_open_with_visible_announce_line(self) -> None:
        expected = {
            ORCHESTRATOR: "Announce first: **Using yrrp-build-campaign",
            FEATURE_OWNER: "Announce first: **Using yrrp-feature-owner",
        }
        for path, prefix in expected.items():
            with self.subTest(path=path):
                _, body = split_agent(path.read_text())
                self.assertTrue(body.lstrip().startswith(prefix))

    def test_signed_release_uses_campaign_launcher(self) -> None:
        body = SIGNED_RELEASE_SKILL.read_text()
        self.assertIn("yrrp-launch-campaign-build.py", body)
        self.assertIn("Do not launch `sign-lineage-build.sh` directly", body)

    def test_project_settings_register_exec_form_hook(self) -> None:
        value = json.loads((ROOT / ".claude/settings.json").read_text())
        handler = value["hooks"]["PreToolUse"][0]
        self.assertEqual("Bash", handler["matcher"])
        hook = handler["hooks"][0]
        self.assertEqual("command", hook["type"])
        self.assertEqual("python3", hook["command"])
        self.assertEqual(10, hook["timeout"])
        self.assertEqual(
            ["${CLAUDE_PROJECT_DIR}/.claude/hooks/yrrp-build-campaign-gate.py"],
            hook["args"],
        )

    def test_runtime_campaigns_are_ignored(self) -> None:
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("/.claude/build-campaigns/", lines)


if __name__ == "__main__":
    unittest.main()

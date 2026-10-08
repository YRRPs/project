from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURE_OWNER = ROOT / ".claude/agents/yrrp-feature-owner.md"
RELEASE_MANAGER = ROOT / ".claude/agents/yrrp-release-manager.md"
FEATURE_OWNER_SKILLS = (
    ROOT / ".claude/skills/yrrp-settings-ui/SKILL.md",
    ROOT / ".claude/skills/rom-feature-research/SKILL.md",
)
REMOVED_AGENT = ROOT / ".claude/agents" / ("yrrp-build-" + "campaign.md")
REMOVED_PATHS = (
    ROOT / ".claude/hooks" / ("yrrp-build-" + "campaign-gate.py"),
    ROOT / ".claude/settings.json",
    ROOT / "scripts" / ("yrrp-build-" + "campaign.py"),
    ROOT / "scripts" / ("yrrp-launch-" + "campaign-build.py"),
    ROOT / "scripts" / ("yrrp_build_" + "campaign"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_model.py"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_store.py"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_service.py"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_cli.py"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_gate.py"),
    ROOT / "tests" / ("test_yrrp_build_" + "campaign_launcher.py"),
    ROOT / "tests" / ("test_yrrp_launch_" + "campaign_build_cli.py"),
)
NEW_ACTIVE_PATHS = (
    ".claude/agents/yrrp-release-manager.md",
    "scripts/yrrp-release.py",
    "scripts/yrrp_release/__init__.py",
    "scripts/yrrp_release/launcher.py",
    "scripts/yrrp_release/receipt.py",
    "tests/test_yrrp_release_launcher.py",
    "tests/test_yrrp_release_receipt.py",
)
RELEASE_CHECKOUT_GUIDANCE = (
    RELEASE_MANAGER,
    ROOT / ".claude/skills/yrrp-signed-ota-release/SKILL.md",
    ROOT / ".claude/skills/yrrp-manifest-and-forks/SKILL.md",
    ROOT / "docs/build/environment.md",
    ROOT / "docs/design/downstream-workflow.md",
)
LAUNCH_LOG_GUIDANCE = (
    ROOT / ".claude/skills/yrrp-signed-ota-release/SKILL.md",
    ROOT / ".claude/skills/yrrp-builder-access/SKILL.md",
    ROOT / "docs/build/environment.md",
)
RECOVERY_GUIDANCE = (
    ROOT / ".claude/skills/yrrp-signed-ota-release/SKILL.md",
    ROOT / "docs/build/signing.md",
    ROOT / "docs/design/ota-hosting.md",
)
ACTIVE_GUIDANCE = (
    ROOT / ".claude/skills/yrrp-builder-access/SKILL.md",
    ROOT / ".claude/skills/yrrp-signed-ota-release/SKILL.md",
    ROOT / ".claude/skills/yrrp-manifest-and-forks/SKILL.md",
    FEATURE_OWNER,
    RELEASE_MANAGER,
    ROOT / "docs/build/environment.md",
    ROOT / "docs/build/signing.md",
    ROOT / "docs/design/ota-hosting.md",
    ROOT / "docs/design/downstream-workflow.md",
    ROOT / "README.md",
)

FEATURE_READY_FIELDS = (
    "PR URL",
    "repository",
    "branch",
    "base SHA",
    "tested head SHA",
    "local test evidence",
    "acceptance criteria",
    "PROOF_PLAN",
)
CLAIM_FIELDS = (
    "claim",
    "trigger_and_setup",
    "observable_evidence",
    "expected_outcome",
    "cheapest_proving_layer",
    "limitations",
    "post_release_device_check",
    "restoration",
)
INTAKE_OPTIONS = (
    "- **Keep release open** — wait for more PRs.",
    "- **This is the last feature** — closes intake and proceeds to review and merge.",
)
VERDICTS = ("PROVEN", "FAILED", "UNPROVEN")
RELEASE_DUTIES = (
    "Review every submitted PR against its acceptance criteria, evidence, proof plan, and current default branch.",
    "Before each merge, compute a stable patch ID for the tested PR diff. Merge only the selected PRs, capture each actual merged default-branch SHA, compute the merged diff patch ID, and require both patch IDs to match; otherwise return the PR for retesting.",
    "Fetch origin and create a clean dedicated release worktree beneath `.workdirs/`, detached at the exact merged project SHA.",
    "Run the fixed `prepare` command from that release checkout with the project SHA and every selected repository SHA.",
    "Present the returned project, repository, local-manifest filename/hash, and revision-locked manifest evidence through `AskUserQuestion` with the exact approval option `Build and release`.",
    "Only after that approval, run the fixed `launch` command with the same SHAs and exact returned manifest SHA-256.",
    "Monitor the approved build, then verify checksums, signatures, container health, public endpoints, and device behavior.",
    "Coordinate every proof claim serially and assign exactly one verdict.",
    "Create one immutable Markdown receipt under `.claude/releases/<build-id>.md` with no-overwrite semantics.",
)
OBSOLETE_RUNTIME_TOKENS = (
    "yrrp-build-" + "campaign",
    "yrrp-launch-" + "campaign-build",
    "REGISTER_" + "FEATURE",
    "READY_FOR_" + "BUILD",
    "DEVICE_LEASE_" + "GRANTED",
    "authorize-" + "recovery",
    "YRRP_CAMPAIGN_" + "CLAIM_FILE",
    "yrrp-build-" + "campaign-gate",
)


def split_agent(text: str) -> tuple[str, str]:
    first, frontmatter, body = text.split("---", 2)
    if first.strip():
        raise ValueError("agent file must start with frontmatter")
    return frontmatter, body


def frontmatter_value(frontmatter: str, key: str) -> str:
    prefix = f"{key}: "
    values = [line.removeprefix(prefix) for line in frontmatter.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        raise ValueError(f"expected exactly one {key!r} key")
    return values[0]


def frontmatter_tools(frontmatter: str) -> set[str]:
    lines = frontmatter.splitlines()
    starts = [index + 1 for index, line in enumerate(lines) if line == "tools:"]
    if len(starts) != 1:
        raise ValueError("expected exactly one tools block")

    tools: set[str] = set()
    for line in lines[starts[0] :]:
        if not line.startswith("  - "):
            break
        tools.add(line.removeprefix("  - "))
    return tools


def fenced_protocol_fields(body: str, protocol: str) -> tuple[str, ...]:
    lines = body.splitlines()
    starts = [
        index
        for index, line in enumerate(lines[:-1])
        if line == "```text" and lines[index + 1] == protocol
    ]
    if len(starts) != 1:
        raise ValueError(f"expected exactly one fenced {protocol} contract")

    fields: list[str] = []
    for line in lines[starts[0] + 2 :]:
        if line == "```":
            return tuple(fields)
        if not line.endswith(":"):
            raise ValueError(f"invalid {protocol} field line: {line!r}")
        fields.append(line.removesuffix(":"))
    raise ValueError(f"unterminated fenced {protocol} contract")


def anchored_bullets(body: str, heading: str) -> tuple[str, ...]:
    lines = body.splitlines()
    starts = [index + 1 for index, line in enumerate(lines) if line == heading]
    if len(starts) != 1:
        raise ValueError(f"expected exactly one {heading!r} heading")

    bullets: list[str] = []
    for line in lines[starts[0] :]:
        if not line and not bullets:
            continue
        if line.startswith("- "):
            bullets.append(line)
            continue
        if bullets:
            break
    return tuple(bullets)


def numbered_steps(body: str, heading: str) -> tuple[str, ...]:
    lines = body.splitlines()
    starts = [index + 1 for index, line in enumerate(lines) if line == heading]
    if len(starts) != 1:
        raise ValueError(f"expected exactly one {heading!r} heading")

    steps: list[str] = []
    for line in lines[starts[0] :]:
        if not line and not steps:
            continue
        expected_prefix = f"{len(steps) + 1}. "
        if line.startswith(expected_prefix):
            steps.append(line.removeprefix(expected_prefix))
            continue
        if steps:
            break
    return tuple(steps)


def backtick_values(line: str, prefix: str) -> tuple[str, ...]:
    if not line.startswith(prefix):
        raise ValueError(f"line does not start with {prefix!r}")
    return tuple(re.findall(r"`([^`]+)`", line.removeprefix(prefix)))


def active_repository_paths() -> tuple[Path, ...]:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    relative_paths = set(tracked) | set(NEW_ACTIVE_PATHS)
    return tuple(
        ROOT / relative
        for relative in sorted(relative_paths)
        if (ROOT / relative).is_file()
    )


def stale_runtime_occurrences(paths: tuple[Path, ...]) -> list[tuple[Path, str]]:
    stale_log_path = "/home/android/" + "signed-build.log"
    occurrences: list[tuple[Path, str]] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for token in (*OBSOLETE_RUNTIME_TOKENS, stale_log_path):
            if token in text:
                occurrences.append((path, token))
    return occurrences


class AgentDefinitionTest(unittest.TestCase):
    def test_runtime_directories_are_ignored(self) -> None:
        lines = (ROOT / ".gitignore").read_text().splitlines()
        for path in (
            "/.workdirs/",
            "/.claude/releases/",
            "/.claude/build-campaigns/",
        ):
            self.assertIn(path, lines)

    def test_old_campaign_runtime_is_removed(self) -> None:
        self.assertFalse(REMOVED_AGENT.exists())
        self.assertTrue(RELEASE_MANAGER.exists())
        for path in REMOVED_PATHS:
            with self.subTest(path=path):
                self.assertFalse(path.exists())

    def test_active_guidance_names_release_manager_and_stateless_cli(self) -> None:
        combined = "\n".join(path.read_text() for path in ACTIVE_GUIDANCE)
        self.assertIn("yrrp-release-manager", combined)
        self.assertIn("python3 scripts/yrrp-release.py prepare", combined)
        self.assertIn("python3 scripts/yrrp-release.py launch", combined)
        self.assertIn("--manifest-sha256", combined)
        self.assertIn("Keep release open", combined)
        self.assertIn("This is the last feature", combined)
        for verdict in VERDICTS:
            self.assertIn(verdict, combined)

    def test_active_guidance_has_no_obsolete_runtime_tokens(self) -> None:
        for path in ACTIVE_GUIDANCE:
            text = path.read_text()
            for token in OBSOLETE_RUNTIME_TOKENS:
                with self.subTest(path=path, token=token):
                    self.assertNotIn(token, text)

    def test_active_repository_files_have_no_obsolete_runtime_tokens(self) -> None:
        self.assertEqual([], stale_runtime_occurrences(active_repository_paths()))

    def test_stale_runtime_scanner_detects_an_enumerated_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_file = Path(directory) / "yrrp-release.py"
            stale_token = "yrrp-build-" + "campaign"
            active_file.write_text(f"runtime = {stale_token!r}\n")
            self.assertEqual(
                [(active_file, stale_token)],
                stale_runtime_occurrences((active_file,)),
            )

    def test_feature_owner_is_a_dedicated_main_session(self) -> None:
        frontmatter, body = split_agent(FEATURE_OWNER.read_text())
        self.assertEqual("yrrp-feature-owner", frontmatter_value(frontmatter, "name"))
        self.assertIn("model: inherit", frontmatter)
        self.assertNotIn("tools:", frontmatter)
        self.assertIn(
            "claude --agent yrrp-feature-owner --name yrrp-feature-<feature-id>",
            body,
        )
        self.assertIn("`--name` is the display name shown by `ListAgents`", body)
        self.assertIn("dedicated main session", body)

    def test_feature_owner_uses_isolated_workdirs_from_remote_default(self) -> None:
        body = FEATURE_OWNER.read_text().lower()
        for phrase in (
            "verify `/.workdirs/` is ignored",
            "one canonical clone per repository",
            ".workdirs/repos/<repository>",
            "clone into that canonical path only when it is missing",
            "never clone once per feature",
            "verify its `origin` url",
            "fetch and prune the remote",
            "current symbolic default branch",
            "current default-branch sha",
            ".workdirs/locks/<repository>.lock",
            "git worktree add",
            ".workdirs/features/<feature-id>/<repository>",
            "never implement in the canonical clone",
            "fetched default-branch ref",
            "explicit cleanup",
        ):
            self.assertIn(phrase, body)

    def test_feature_owner_owns_pr_delivery_without_release_operations(self) -> None:
        body = FEATURE_OWNER.read_text()
        lowered = body.lower()
        for responsibility in (
            "brainstorm",
            "specification",
            "implementation plan",
            "tdd",
            "review",
            "push",
            "open the pr",
        ):
            self.assertIn(responsibility, lowered)
        for forbidden_operation in (
            "SSH to `AndroidBuilder`",
            "mutate builder source",
            "product build",
            "sign",
            "deploy",
            "merge the PR",
            "write the release receipt",
        ):
            self.assertIn(forbidden_operation, body)

    def test_feature_ready_contract_matches_between_producer_and_consumer(self) -> None:
        for path in (FEATURE_OWNER, RELEASE_MANAGER):
            with self.subTest(path=path):
                body = split_agent(path.read_text())[1]
                self.assertEqual(FEATURE_READY_FIELDS, fenced_protocol_fields(body, "FEATURE_READY"))

    def test_feature_proof_plan_has_exact_claim_fields(self) -> None:
        body = split_agent(FEATURE_OWNER.read_text())[1]
        bullets = anchored_bullets(body, "Each claim contains exactly:")
        self.assertEqual(tuple(f"- `{field}`" for field in CLAIM_FIELDS), bullets)
        self.assertIn("Local checks must state what they cannot prove", body)

    def test_feature_proof_examples_cover_real_device_boundaries(self) -> None:
        body = FEATURE_OWNER.read_text()
        for example in (
            "CRT",
            "visible renderer",
            "lifecycle restore",
            "Pulse",
            "real media energy",
            "rendering",
            "teardown",
            "incremental OTA",
            "delta",
            "application",
            "boot",
        ):
            self.assertIn(example, body)
        self.assertIn("Skip TalkBack and accessibility acceptance", body)

    def test_tool_parser_rejects_duplicate_blocks(self) -> None:
        duplicate = "tools:\n  - Read\ntools:\n  - Bash\n"
        with self.assertRaisesRegex(ValueError, "exactly one tools block"):
            frontmatter_tools(duplicate)

    def test_release_manager_has_exact_identity_and_tool_boundary(self) -> None:
        frontmatter, body = split_agent(RELEASE_MANAGER.read_text())
        self.assertEqual("yrrp-release-manager", frontmatter_value(frontmatter, "name"))
        self.assertIn(
            "claude --agent yrrp-release-manager --name yrrp-release-manager",
            body,
        )
        self.assertIn("`--name` makes `ListAgents` discovery deterministic", body)
        self.assertIn("dedicated main session", body)

        self.assertEqual(1, frontmatter.splitlines().count("tools:"))
        tools = frontmatter_tools(frontmatter)
        self.assertEqual(
            {
                "Read",
                "Grep",
                "Glob",
                "Bash",
                "Skill",
                "ListAgents",
                "SendMessage",
                "AskUserQuestion",
                "Monitor",
                "PushNotification",
            },
            tools,
        )

    def test_release_manager_description_matches_receipt_boundary(self) -> None:
        frontmatter, body = split_agent(RELEASE_MANAGER.read_text())
        description = frontmatter_value(frontmatter, "description")
        self.assertIn("NOT for feature implementation or source editing", description)
        self.assertNotIn("editing repository files", description)
        self.assertIn("fixed release CLI", body)
        self.assertIn("receipt creation", body)

    def test_release_manager_owns_merge_release_and_receipt(self) -> None:
        body = split_agent(RELEASE_MANAGER.read_text())[1]
        self.assertEqual(
            RELEASE_DUTIES,
            numbered_steps(body, "## Review, merge, build, and verify"),
        )
        claim_bullets = anchored_bullets(body, "Each proof claim contains exactly:")
        self.assertEqual(tuple(f"- `{field}`" for field in CLAIM_FIELDS), claim_bullets)
        verdict_line = next(
            line for line in body.splitlines() if line.startswith("Assign each claim exactly one verdict:")
        )
        self.assertEqual(VERDICTS, backtick_values(verdict_line, "Assign each claim exactly one verdict:"))
        self.assertIn("without lease state", body)

    def test_release_intake_gate_is_binary_and_transient(self) -> None:
        body = split_agent(RELEASE_MANAGER.read_text())[1]
        heading = "After each accepted `FEATURE_READY`, ask one transient two-choice `AskUserQuestion`:"
        self.assertEqual(INTAKE_OPTIONS, anchored_bullets(body, heading))
        self.assertIn("Build approval is separate", body)
        self.assertIn("after merged default-branch SHAs are known", body)

    def test_release_manager_uses_prepare_evidence_then_exact_launch(self) -> None:
        body = RELEASE_MANAGER.read_text()
        for phrase in (
            "python3 scripts/yrrp-release.py prepare --project-sha",
            "project SHA",
            "repository SHAs",
            "manifest SHA-256",
            "AskUserQuestion",
            "Build and release",
            "python3 scripts/yrrp-release.py launch",
            "--manifest-sha256",
        ):
            self.assertIn(phrase, body)
        prepare = body.index("yrrp-release.py prepare")
        approval = body.index("**Build and release** — launch")
        launch = body.index("yrrp-release.py launch")
        self.assertLess(prepare, approval)
        self.assertLess(approval, launch)

    def test_release_runs_from_clean_exact_sha_worktree(self) -> None:
        for path in RELEASE_CHECKOUT_GUIDANCE:
            text = path.read_text().lower()
            with self.subTest(path=path):
                self.assertIn("fetch origin", text)
                self.assertIn("clean dedicated release worktree", text)
                self.assertIn("beneath `.workdirs/`", text)
                self.assertIn("detached at the exact merged project sha", text)
                self.assertIn("run `prepare` and `launch` from that checkout", text)
                self.assertIn("retain it through release completion", text)
                self.assertIn("no automatic cleanup", text)

    def test_launch_monitoring_uses_returned_log_path(self) -> None:
        stale_path = "/home/android/" + "signed-build.log"
        for path in LAUNCH_LOG_GUIDANCE:
            text = path.read_text()
            with self.subTest(path=path):
                self.assertNotIn(stale_path, text)
                self.assertIn("exact `log` path returned by `yrrp-release.py launch`", text)
                self.assertIn("/opt/android/out/signed/yrrp-ota-build-<timestamp>.log", text)
                self.assertIn("/home/android/signed-build.status", text)

    def test_recovery_scripts_own_the_shared_lock_contract(self) -> None:
        forbidden = "lock-free " + "recovery"
        for path in RECOVERY_GUIDANCE:
            text = path.read_text().lower()
            with self.subTest(path=path):
                self.assertNotIn(forbidden, text)
                self.assertIn("directly invokes the fixed", text)
                self.assertIn("non-blockingly acquires and holds", text)
                self.assertIn("for the entire operation", text)
                self.assertIn("refuses if the lock is busy", text)
                self.assertIn("read-only inspection may diagnose", text)
                self.assertIn("is not the locking mechanism", text)

    def test_feature_owner_never_invokes_builder_skill(self) -> None:
        body = FEATURE_OWNER.read_text()
        self.assertIn("Never invoke `yrrp-builder-access`", body)

    def test_feature_owner_skills_delegate_remote_checks_to_release_manager(self) -> None:
        for path in FEATURE_OWNER_SKILLS:
            text = path.read_text()
            with self.subTest(path=path):
                self.assertIn("yrrp-release-manager", text)
                self.assertIn("Feature owners never invoke `yrrp-builder-access`", text)
                self.assertNotIn("Run long builds through yrrp-builder-access", text)

    def test_legacy_protocol_is_absent_from_agent_definitions(self) -> None:
        combined = FEATURE_OWNER.read_text() + RELEASE_MANAGER.read_text()
        for term in OBSOLETE_RUNTIME_TOKENS:
            self.assertNotIn(term, combined)
        for phrase in (
            "campaign phase",
            "campaign state",
            "device lease",
            "command digest",
            "preflight authorization",
            "registration",
        ):
            self.assertNotIn(phrase, combined.lower())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.command_policy import (
    is_read_only_builder_command,
    is_supported_recovery_command,
)
from yrrp_build_campaign.service import CAMPAIGN_AGENT, CampaignService
from yrrp_build_campaign.store import CampaignStore

LAUNCHER = "scripts/yrrp-launch-campaign-build.py"
CAMPAIGN_CLI = "yrrp-build-campaign.py"


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def _is_builder_ssh(command: str) -> bool:
    tokens = _tokens(command)
    return "ssh" in tokens and "AndroidBuilder" in tokens


def _shell_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(character in ";&|()" for character in token):
            segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _segment_runs_signer(segment: list[str]) -> bool:
    command = Path(segment[0]).name
    if command == "sign-lineage-build.sh":
        return True
    if command in {"bash", "sh"} and "-c" in segment:
        index = segment.index("-c")
        return index + 1 < len(segment) and _is_direct_signing(segment[index + 1])
    wrappers = {"command", "env", "exec", "flock", "nohup", "sudo"}
    return command in wrappers and any(
        Path(token).name == "sign-lineage-build.sh" for token in segment[1:]
    )


def _is_direct_signing(command: str) -> bool:
    return any(_segment_runs_signer(segment) for segment in _shell_segments(command))


def _is_recovery_authorization(command: str) -> bool:
    """Match the campaign CLI's authorize-recovery subcommand anywhere in a pipeline."""
    tokens = [
        part
        for token in _tokens(command)
        for part in re.split(r"[;&|()]+", token)
        if part
    ]
    return any(
        Path(token).name == CAMPAIGN_CLI and tokens[index + 1 : index + 2] == ["authorize-recovery"]
        for index, token in enumerate(tokens)
    )


def launcher_campaign_id(command: str) -> str | None:
    tokens = _tokens(command)
    if len(tokens) != 4:
        return None
    executable, script, option, campaign_id = tokens
    if Path(executable).name not in {"python", "python3"}:
        return None
    if script not in {LAUNCHER, str(PROJECT_ROOT / LAUNCHER)}:
        return None
    if option != "--campaign-id":
        return None
    return campaign_id


def classify(command: str) -> str:
    if _is_recovery_authorization(command):
        return "recovery-authorization"
    return _classify_build(command)


def _classify_build(command: str) -> str:
    if launcher_campaign_id(command) is not None:
        return "launcher"
    if _is_direct_signing(command):
        return "builder"
    if _is_builder_ssh(command):
        if is_read_only_builder_command(command):
            return "readonly"
        return "builder"
    return "other"


def decision(value: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": value,
            "permissionDecisionReason": reason,
        }
    }


def campaign_root() -> Path:
    configured = os.environ.get("YRRP_CAMPAIGN_ROOT")
    if configured:
        return Path(configured)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", PROJECT_ROOT))
    return project / ".claude" / "build-campaigns"


def evaluate(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("tool_name") != "Bash":
        return None
    command = str(event["tool_input"]["command"])
    kind = classify(command)
    if kind == "recovery-authorization":
        return _gate_recovery_authorization(event, command)
    if kind in {"other", "readonly"}:
        return None
    service = CampaignService(CampaignStore(campaign_root()))
    try:
        campaign_id = service.active_campaign_id()
        if kind == "launcher":
            return _authorize_launcher(event, service, campaign_id, command)
        if is_supported_recovery_command(command):
            return _authorize_builder_recovery(event, service, campaign_id, command)
        return _authorize_builder_preflight(service, campaign_id, command)
    except Exception as error:
        return decision("deny", _denial_reason(error))


def _gate_recovery_authorization(event: dict[str, Any], command: str) -> dict[str, Any] | None:
    agent_type = str(event.get("agent_type", ""))
    if agent_type != CAMPAIGN_AGENT:
        return decision(
            "deny",
            f"Build campaign gate: only the {CAMPAIGN_AGENT} session may run "
            f"authorize-recovery, not agent type {agent_type or '<none>'!r}.",
        )
    if _classify_build(command) != "other":
        return decision(
            "deny",
            "Build campaign gate: run authorize-recovery on its own, "
            "without a builder, signing, or launcher command in the same Bash call.",
        )
    return None


def _authorize_launcher(
    event: dict[str, Any],
    service: CampaignService,
    campaign_id: str,
    command: str,
) -> dict[str, Any]:
    requested = launcher_campaign_id(command)
    if requested != campaign_id:
        raise ValueError("launcher campaign does not match active campaign")
    actor = str(event["session_id"])
    agent_type = str(event.get("agent_type", ""))
    service.authorize_launcher(campaign_id, actor, agent_type)
    return decision("allow", f"Authorized fixed launcher for campaign {campaign_id}")


def _authorize_builder_recovery(
    event: dict[str, Any],
    service: CampaignService,
    campaign_id: str,
    command: str,
) -> dict[str, Any]:
    agent_type = str(event.get("agent_type", ""))
    try:
        service.consume_recovery(campaign_id, command, agent_type)
    except ValueError as error:
        raise ValueError(
            f"recovery command refused: {error}; only the {CAMPAIGN_AGENT} session may "
            "run a recovery it authorized once with authorize-recovery"
        ) from error
    return decision("allow", f"Authorized one-time recovery command for campaign {campaign_id}")


def _authorize_builder_preflight(
    service: CampaignService,
    campaign_id: str,
    command: str,
) -> dict[str, Any]:
    try:
        service.consume_preflight(campaign_id, command)
    except ValueError as error:
        raise ValueError(
            "raw builder command is not an authorized one-time preflight; "
            "product builds must use scripts/yrrp-launch-campaign-build.py"
        ) from error
    return decision("allow", f"Authorized preflight command for campaign {campaign_id}")


def _denial_reason(error: Exception) -> str:
    recovery = (
        "Use scripts/yrrp-build-campaign.py status, authorize an exact preflight, "
        "or use scripts/yrrp-launch-campaign-build.py from yrrp-build-campaign."
    )
    return f"Build campaign gate: {error}. {recovery}"


def main() -> int:
    try:
        event = json.load(sys.stdin)
        output = evaluate(event)
    except Exception as error:
        output = decision("deny", f"Build campaign gate failed closed: {error}")
    if output is None:
        return 0
    try:
        payload = json.dumps(output, sort_keys=True).encode()
        os.write(1, payload + b"\n")
    except Exception as error:
        print(f"Build campaign gate could not emit denial: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

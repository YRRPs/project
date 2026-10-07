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

from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore

SIGNING_SCRIPT = re.compile(r"(?:^|[\s'\"/])sign-lineage-build\.sh(?:[\s;'\"]|$)")
READ_ONLY_BUILDER = re.compile(
    r"\b(?:cat|tail|head|ps|pgrep|sha256sum|git\s+(?:status|rev-parse)|"
    r"screen\s+-list|docker\s+inspect)\b"
)
LAUNCHER = "scripts/yrrp-launch-campaign-build.py"


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def _is_builder_ssh(command: str) -> bool:
    tokens = _tokens(command)
    return "ssh" in tokens and "AndroidBuilder" in tokens


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
    if launcher_campaign_id(command) is not None:
        return "launcher"
    if SIGNING_SCRIPT.search(command):
        return "builder"
    if _is_builder_ssh(command):
        if READ_ONLY_BUILDER.search(command):
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
    if kind in {"other", "readonly"}:
        return None
    service = CampaignService(CampaignStore(campaign_root()))
    try:
        campaign_id = service.active_campaign_id()
        if kind == "launcher":
            return _authorize_launcher(event, service, campaign_id, command)
        return _authorize_builder_preflight(service, campaign_id, command)
    except Exception as error:
        return decision("deny", _denial_reason(error))


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

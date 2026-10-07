#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore

EXPENSIVE = (
    re.compile(r"(?:^|[\s'\"/])sign-lineage-build\.sh(?:[\s'\"]|$)"),
    re.compile(r"\bbrunch\s+salami\b"),
    re.compile(
        r"\bmka\b[^\n]*(?:target-files-package|otatools|otapackage|bacon)\b"
    ),
)
PREFLIGHT = (
    re.compile(r"\batest\b"),
    re.compile(r"\bm\s+(?:SystemUI(?:-core)?|Settings|SettingsRoboTests)\b"),
)


def classify(command: str) -> str:
    if any(pattern.search(command) for pattern in EXPENSIVE):
        return "expensive"
    if "ssh AndroidBuilder" in command and any(
        pattern.search(command) for pattern in PREFLIGHT
    ):
        return "preflight"
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
    if kind == "other":
        return None
    service = CampaignService(CampaignStore(campaign_root()))
    try:
        campaign_id = service.active_campaign_id()
        reason = _authorize(service, campaign_id, kind, command)
        return decision("allow", reason)
    except (KeyError, TypeError, ValueError, FileNotFoundError) as error:
        recovery = (
            "Use scripts/yrrp-build-campaign.py status, then authorize the "
            "preflight command or freeze the active campaign."
        )
        return decision("deny", f"Build campaign gate: {error}. {recovery}")


def _authorize(
    service: CampaignService,
    campaign_id: str,
    kind: str,
    command: str,
) -> str:
    if kind == "preflight":
        service.consume_preflight(campaign_id, command)
        return f"Authorized preflight command for campaign {campaign_id}"
    service.claim_build(campaign_id, command)
    return f"Claimed frozen build for campaign {campaign_id}"


def main() -> int:
    try:
        event = json.load(sys.stdin)
        output = evaluate(event)
    except (
        KeyError,
        TypeError,
        ValueError,
        FileNotFoundError,
    ) as error:
        output = decision("deny", f"Build campaign gate failed closed: {error}")
    if output is not None:
        json.dump(output, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

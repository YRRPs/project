#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from yrrp_build_campaign.launcher import collect_remote_source, launch_campaign
from yrrp_build_campaign.service import CampaignService
from yrrp_build_campaign.store import CampaignStore


def campaign_root() -> Path:
    configured = os.environ.get("YRRP_CAMPAIGN_ROOT")
    if configured:
        return Path(configured)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd()))
    return project / ".claude" / "build-campaigns"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch one frozen YRRP build campaign")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--capture-snapshot", action="store_true")
    arguments = parser.parse_args()
    service = CampaignService(CampaignStore(campaign_root()))
    try:
        if arguments.capture_snapshot:
            campaign = service.store.load(arguments.campaign_id)
            live_source, manifest = collect_remote_source(campaign)
            manifest_name = f"manifest-{live_source['manifest_sha256'][:12]}.xml"
            live_source["manifest_evidence"] = service.store.write_evidence(
                arguments.campaign_id,
                manifest_name,
                manifest.encode(),
            )
            prepared = service.prepare_snapshot(arguments.campaign_id, live_source)
            print(json.dumps(prepared, sort_keys=True))
        else:
            launch_campaign(service, arguments.campaign_id)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"campaign launch error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

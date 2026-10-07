from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from .model import Campaign
from .service import CampaignService

REMOTE_SNAPSHOT_SCRIPT = r'''
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ANDROID = Path("/opt/android")
PROJECT = Path("/opt/yrrp/project")
repositories = json.load(sys.stdin)


def run(*args, cwd):
    return subprocess.run(
        list(args),
        cwd=cwd,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


manifest = run(str(ANDROID / ".repo/repo/repo"), "manifest", "-r", cwd=ANDROID)
result = {
    "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
    "repositories": {},
    "branches": {},
    "clean_repositories": [],
    "project_sha": run("git", "rev-parse", "HEAD", cwd=PROJECT),
}
for relative in repositories:
    root = ANDROID / relative
    result["repositories"][relative] = run("git", "rev-parse", "HEAD", cwd=root)
    result["branches"][relative] = run(
        "git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root
    )
    if not run("git", "status", "--porcelain", cwd=root):
        result["clean_repositories"].append(relative)
print(json.dumps(result, sort_keys=True))
'''

REMOTE_BUILD_SCRIPT = r'''set -euo pipefail
sudo install -d -m 0777 -o root -g utmp /run/screen
if screen -list | grep -q '[.]yrrp-ota-build'; then
    echo 'yrrp-ota-build screen already exists' >&2
    exit 1
fi
mkdir -p /opt/android/out/signed
stamp=$(date +%Y%m%d-%H%M%S)
log=/opt/android/out/signed/yrrp-ota-build-${stamp}.log
screen -L -Logfile "$log" -dmS yrrp-ota-build \
    bash -lc 'exec /opt/yrrp/project/scripts/sign-lineage-build.sh'
printf 'log=%s\nproject=%s\n' \
    "$log" "$(git -C /opt/yrrp/project rev-parse HEAD)"
'''

SnapshotProvider = Callable[[Campaign], dict[str, Any]]
BuildRunner = Callable[[], None]


def collect_remote_snapshot(campaign: Campaign) -> dict[str, Any]:
    base_snapshot = campaign.source_snapshot or {
        "repositories": campaign.expected_revisions()
    }
    repositories = sorted(base_snapshot["repositories"])
    result = subprocess.run(
        ["ssh", "AndroidBuilder", "python3", "-c", REMOTE_SNAPSHOT_SCRIPT],
        input=json.dumps(repositories),
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    )
    live = json.loads(result.stdout)
    return {
        **base_snapshot,
        **live,
    }


def launch_remote_build() -> None:
    subprocess.run(
        ["ssh", "AndroidBuilder", "bash", "-s"],
        input=REMOTE_BUILD_SCRIPT,
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    )


def launch_campaign(
    service: CampaignService,
    campaign_id: str,
    snapshot_provider: SnapshotProvider = collect_remote_snapshot,
    build_runner: BuildRunner = launch_remote_build,
) -> Campaign:
    authorization = service.consume_launcher_authorization(campaign_id)
    campaign = service.store.load(campaign_id)
    current_snapshot = snapshot_provider(campaign)
    claimed = service.claim_build(
        campaign_id,
        current_snapshot=current_snapshot,
        actor=str(authorization["actor"]),
        agent_type=str(authorization["agent_type"]),
    )
    try:
        build_runner()
    except Exception as error:
        service.record_launch_failure(campaign_id, str(error))
        raise
    return claimed

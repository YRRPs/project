from __future__ import annotations

import json
import shlex
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
SIGNING = Path("/opt/yrrp/signing")
repositories = json.load(sys.stdin)


def run(*args, cwd):
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, check=True, text=True
    ).stdout.strip()


def build_process_active(processes):
    import shlex

    builds = {"soong_ui", "ninja", "ota_from_target_files", "sign_target_files_apks"}

    def program(token):
        name = token.rsplit("/", 1)[-1]
        for suffix in (".bash", ".sh", ".py"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    for line in processes.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        names = [program(token) for token in tokens[:2]]
        if names and names[0] in builds:
            return True
        interpreter = names[:1] and (
            names[0] in ("bash", "sh") or names[0].startswith("python")
        )
        if interpreter and len(names) == 2 and names[1] in builds:
            return True
    return False


def validate_preflight():
    subprocess.run(
        ["sha256sum", "--check", "--quiet", "MANIFEST.sha256"],
        cwd=SIGNING,
        stdout=sys.stderr,
        check=True,
    )
    if (SIGNING / "testkey.pk8").readlink().name != "releasekey.pk8":
        raise SystemExit("testkey.pk8 does not point to releasekey.pk8")
    if (SIGNING / "testkey.x509.pem").readlink().name != "releasekey.x509.pem":
        raise SystemExit("testkey.x509.pem does not point to releasekey.x509.pem")
    if build_process_active(run("ps", "-eo", "args", cwd=ANDROID)):
        raise SystemExit("build or signing process is already active")
    screens = subprocess.run(
        ["screen", "-list"], capture_output=True, text=True
    ).stdout
    if ".yrrp-ota-build" in screens:
        raise SystemExit("yrrp-ota-build screen already exists")
    subprocess.run(
        ["docker", "network", "inspect", "proxy-net"],
        stdout=sys.stderr,
        check=True,
    )


validate_preflight()
manifest = run(str(ANDROID / ".repo/repo/repo"), "manifest", "-r", cwd=ANDROID)
result = {
    "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
    "manifest_xml": manifest,
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

REMOTE_LAUNCH_SCRIPT = r'''
import fcntl
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ANDROID = Path("/opt/android")
PROJECT = Path("/opt/yrrp/project")
SIGNING = Path("/opt/yrrp/signing")
payload = json.load(sys.stdin)
expected = payload["snapshot"]
campaign_id = payload["campaign_id"]
repositories = sorted(expected["repositories"])


def run(*args, cwd):
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, check=True, text=True
    ).stdout.strip()


def build_process_active(processes):
    import shlex

    builds = {"soong_ui", "ninja", "ota_from_target_files", "sign_target_files_apks"}

    def program(token):
        name = token.rsplit("/", 1)[-1]
        for suffix in (".bash", ".sh", ".py"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    for line in processes.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        names = [program(token) for token in tokens[:2]]
        if names and names[0] in builds:
            return True
        interpreter = names[:1] and (
            names[0] in ("bash", "sh") or names[0].startswith("python")
        )
        if interpreter and len(names) == 2 and names[1] in builds:
            return True
    return False


def validate_preflight():
    subprocess.run(
        ["sha256sum", "--check", "--quiet", "MANIFEST.sha256"],
        cwd=SIGNING,
        stdout=sys.stderr,
        check=True,
    )
    if (SIGNING / "testkey.pk8").readlink().name != "releasekey.pk8":
        raise SystemExit("testkey.pk8 does not point to releasekey.pk8")
    if (SIGNING / "testkey.x509.pem").readlink().name != "releasekey.x509.pem":
        raise SystemExit("testkey.x509.pem does not point to releasekey.x509.pem")
    if build_process_active(run("ps", "-eo", "args", cwd=ANDROID)):
        raise SystemExit("build or signing process is already active")
    subprocess.run(
        ["docker", "network", "inspect", "proxy-net"],
        stdout=sys.stderr,
        check=True,
    )


def current_source():
    validate_preflight()
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
    return result


lock_path = Path("/home/android/.yrrp-build-launch.lock")
lock_path.touch(mode=0o600, exist_ok=True)
with lock_path.open("r+") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    current = current_source()
    frozen = {key: expected[key] for key in current}
    if current != frozen:
        raise SystemExit("remote source changed after local verification")
    subprocess.run(
        ["sudo", "install", "-d", "-m", "0777", "-o", "root", "-g", "utmp", "/run/screen"],
        stdout=sys.stderr,
        check=True,
    )
    screens = subprocess.run(
        ["screen", "-list"], capture_output=True, text=True
    ).stdout
    if ".yrrp-ota-build" in screens:
        raise SystemExit("yrrp-ota-build screen already exists")
    output = ANDROID / "out/signed"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log = output / f"yrrp-ota-build-{stamp}.log"
    claim_path = Path("/home/android/.yrrp-campaign-claim.json")
    claim = {
        "campaign_id": campaign_id,
        "source_snapshot_sha256": hashlib.sha256(
            json.dumps(expected, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "expires_at": int(time.time()) + 300,
    }
    claim_path.write_text(json.dumps(claim, sort_keys=True) + "\n")
    claim_path.chmod(0o600)
    subprocess.run(
        [
            "screen", "-L", "-Logfile", str(log), "-dmS", "yrrp-ota-build",
            "flock", "-x", str(lock_path),
            "env", f"YRRP_CAMPAIGN_CLAIM_FILE={claim_path}",
            "/opt/yrrp/project/scripts/sign-lineage-build.sh",
        ],
        stdout=sys.stderr,
        check=True,
    )
    print(json.dumps({"log": str(log), "project": current["project_sha"]}))
'''

SnapshotProvider = Callable[[Campaign], dict[str, Any]]
BuildRunner = Callable[[str, dict[str, Any]], None]


def remote_python_argv(script: str) -> list[str]:
    """Build an ssh argv that runs ``script`` with python3 on the builder.

    ssh joins the remote argv with spaces and the remote login shell parses
    the result, so the script must be shell-quoted to arrive as one argument.
    """
    return ["ssh", "AndroidBuilder", "python3", "-c", shlex.quote(script)]


def collect_remote_source(campaign: Campaign) -> tuple[dict[str, Any], str]:
    base_snapshot = campaign.source_snapshot or {
        "repositories": campaign.expected_revisions()
    }
    repositories = sorted(base_snapshot["repositories"])
    result = subprocess.run(
        remote_python_argv(REMOTE_SNAPSHOT_SCRIPT),
        input=json.dumps(repositories),
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    )
    live = json.loads(result.stdout)
    manifest = str(live.pop("manifest_xml"))
    return {**base_snapshot, **live}, manifest


def collect_remote_snapshot(campaign: Campaign) -> dict[str, Any]:
    snapshot, _ = collect_remote_source(campaign)
    return snapshot


def launch_remote_build(
    campaign_id: str,
    expected_snapshot: dict[str, Any],
) -> None:
    subprocess.run(
        remote_python_argv(REMOTE_LAUNCH_SCRIPT),
        input=json.dumps(
            {"campaign_id": campaign_id, "snapshot": expected_snapshot},
            sort_keys=True,
        ),
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
        build_runner(campaign_id, current_snapshot)
    except Exception as error:
        service.record_launch_failure(campaign_id, str(error))
        raise
    return claimed

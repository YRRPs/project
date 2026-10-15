from __future__ import annotations

import copy
import subprocess
from pathlib import Path

from yrrp_ota.channel import Channel, require_build_id

from .deployment_record import deployment_record_from_receipt
from .jobs import parse_run_id, paths_for_run
from .launcher import PROJECT_ROOT, run_remote, verify_local_project
from .rollback_remote import REMOTE_ROLLBACK_SCRIPT


def rollback_approval(channel: Channel, build_id: str) -> str:
    validated_build = require_build_id(build_id, "rollback build")
    return f"Rollback {channel.name} to {validated_build}"


def local_project_sha(root: Path = PROJECT_ROOT) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    ).stdout.strip()
    if status:
        raise ValueError("local project checkout is dirty")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    ).stdout.strip()
    verify_local_project(sha, root)
    return sha


def start_rollback(
    *,
    approval: str,
    channel: str,
    build_id: str,
    receipt_document: dict[str, object] | None = None,
) -> dict[str, object]:
    parsed_channel = Channel.parse(channel)
    expected_approval = rollback_approval(parsed_channel, build_id)
    if approval != expected_approval:
        raise ValueError(f"rollback requires exact approval {expected_approval!r}")
    project_sha = local_project_sha()
    legacy_record = None
    if receipt_document is not None:
        legacy_record = deployment_record_from_receipt(
            receipt_document, parsed_channel, build_id
        )
    acknowledgment = run_remote(
        REMOTE_ROLLBACK_SCRIPT,
        {
            "project_sha": project_sha,
            "channel": parsed_channel.name,
            "build_id": build_id,
            "legacy_record": legacy_record,
        },
    )
    return _validate_acknowledgment(
        acknowledgment, parsed_channel.name, build_id
    )


def _validate_acknowledgment(
    document: object, expected_channel: str, expected_build_id: str
) -> dict[str, object]:
    if not isinstance(document, dict):
        raise TypeError("rollback acknowledgment must be a JSON object")
    expected_keys = {"run_id", "channel", "build_id", "status", "log"}
    if set(document) != expected_keys:
        raise ValueError("rollback acknowledgment has unexpected keys")
    run_id = document["run_id"]
    if not isinstance(run_id, str) or parse_run_id(run_id) != "rollback":
        raise ValueError("rollback acknowledgment has invalid run ID")
    if document["channel"] != expected_channel:
        raise ValueError("rollback acknowledgment has wrong channel")
    if document["build_id"] != expected_build_id:
        raise ValueError("rollback acknowledgment has wrong build ID")
    paths = paths_for_run(run_id)
    if document["status"] != str(paths.status) or document["log"] != str(paths.log):
        raise ValueError("rollback acknowledgment has untrusted evidence paths")
    return copy.deepcopy(document)

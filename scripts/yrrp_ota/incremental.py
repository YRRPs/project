"""Source checks and output verification for incremental OTAs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .archive import open_unique_zip, read_metadata, read_system_properties, require, sha256
from .channel import VANILLA, Channel
from .naming import BUILD_ID_PATTERN, INCREMENTAL_PATTERN, SHA256_PATTERN

METADATA = "incremental OTA metadata"


def build_incremental(target_files: Path) -> str:
    with open_unique_zip(target_files) as archive:
        properties = read_system_properties(archive)
    value = require(properties, "ro.build.version.incremental", "target-files SYSTEM build.prop")
    if not INCREMENTAL_PATTERN.fullmatch(value):
        raise ValueError(f"ro.build.version.incremental is not numeric: {value}")
    return value


def check_source(target_files: Path, release_json: Path) -> dict[str, str]:
    release = json.loads(release_json.read_text(encoding="utf-8"))
    expected = str((release.get("target_files") or {}).get("sha256", ""))
    if not SHA256_PATTERN.fullmatch(expected):
        raise ValueError("live release.json has no target-files SHA-256")
    actual = sha256(target_files)
    if actual != expected:
        raise ValueError("source target-files SHA-256 does not match live release.json")
    return {
        "source_incremental": build_incremental(target_files),
        "source_target_files_sha256": actual,
    }


def check_identity(
    incremental_ota: Path,
    source_build_id: str,
    target_build_id: str,
    source_incremental: str,
    source_target_files_sha256: str,
    *,
    channel: Channel = VANILLA,
) -> None:
    for build_id in (source_build_id, target_build_id):
        if not BUILD_ID_PATTERN.fullmatch(build_id):
            raise ValueError(f"build ID must match YYYYMMDD-HHMMSS: {build_id}")
    if incremental_ota.name != channel.incremental_ota_name(source_build_id, target_build_id):
        raise ValueError("incremental OTA filename does not match channel, source, and target build IDs")
    if not INCREMENTAL_PATTERN.fullmatch(source_incremental):
        raise ValueError("source incremental must be numeric")
    if not SHA256_PATTERN.fullmatch(source_target_files_sha256):
        raise ValueError("source target-files SHA-256 is invalid")


def verify_output(
    incremental_ota: Path,
    target_files: Path,
    *,
    source_build_id: str,
    target_build_id: str,
    source_incremental: str,
    source_target_files_sha256: str,
    channel: Channel = VANILLA,
) -> dict[str, Any]:
    check_identity(
        incremental_ota,
        source_build_id,
        target_build_id,
        source_incremental,
        source_target_files_sha256,
        channel=channel,
    )
    with open_unique_zip(incremental_ota) as archive:
        metadata = read_metadata(archive, METADATA)
    with open_unique_zip(target_files) as archive:
        target_properties = read_system_properties(archive)
    if require(metadata, "ota-type", METADATA) != "AB":
        raise ValueError("incremental OTA metadata must declare ota-type=AB")
    if require(metadata, "pre-build-incremental", METADATA) != source_incremental:
        raise ValueError("incremental pre-build-incremental does not match source build")
    target_timestamp = require(target_properties, "ro.build.date.utc", "target-files SYSTEM build.prop")
    if require(metadata, "post-timestamp", METADATA) != target_timestamp:
        raise ValueError("incremental post-timestamp does not match target build")
    return {
        "filename": incremental_ota.name,
        "sha256": sha256(incremental_ota),
        "size": incremental_ota.stat().st_size,
        "source_build_id": source_build_id,
        "source_incremental": source_incremental,
        "source_target_files_sha256": source_target_files_sha256,
    }


def load_meta(incremental_ota: Path, target_build_id: str, *, channel: Channel = VANILLA) -> dict[str, Any]:
    """Read the meta JSON written next to the zip and check it still matches the zip."""
    meta_path = incremental_ota.with_name(incremental_ota.name + ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    check_identity(
        incremental_ota,
        str(meta.get("source_build_id", "")),
        target_build_id,
        str(meta.get("source_incremental", "")),
        str(meta.get("source_target_files_sha256", "")),
        channel=channel,
    )
    if meta.get("filename") != incremental_ota.name:
        raise ValueError("incremental meta filename does not match zip")
    if meta.get("sha256") != sha256(incremental_ota) or meta.get("size") != incremental_ota.stat().st_size:
        raise ValueError("incremental SHA-256 or size does not match meta")
    return meta

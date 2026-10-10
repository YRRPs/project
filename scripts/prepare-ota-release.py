#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yrrp_ota.archive import (  # noqa: E402
    open_unique_zip,
    parse_properties,
    read_metadata,
    read_system_properties,
    require,
    sha256,
    write_json,
)
from yrrp_ota.channel import BUILD_ID_PATTERN, LINEAGE_VERSION, VANILLA, Channel  # noqa: E402
from yrrp_ota.incremental import load_meta  # noqa: E402

RELEASE_TYPE = "UNOFFICIAL"
RELEASE_SCHEMA = 3
DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
IMAGE_NAMES = (
    "boot.img",
    "dtbo.img",
    "init_boot.img",
    "vbmeta.img",
    "vendor_boot.img",
    "recovery.img",
)


def validate_public_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("public base URL must use absolute HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("public base URL must not contain credentials, query, or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("public base URL must not contain a path")
    return value.rstrip("/")


def copy_member(archive: zipfile.ZipFile, member: str, destination: Path) -> None:
    try:
        info = archive.getinfo(member)
    except KeyError as error:
        raise ValueError(f"missing required target-files member: {member}") from error
    if info.is_dir() or info.file_size == 0:
        raise ValueError(f"required target-files member is empty: {member}")
    with archive.open(info) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def validate_metadata(metadata: dict[str, str]) -> dict[str, object]:
    if require(metadata, "ota-type", "OTA metadata") != "AB":
        raise ValueError("OTA metadata must declare ota-type=AB")
    post_build = require(metadata, "post-build", "OTA metadata")
    if not post_build.endswith("release-keys"):
        raise ValueError("OTA post-build must end in release-keys")
    timestamp = int(require(metadata, "post-timestamp", "OTA metadata"))
    sdk = int(require(metadata, "post-sdk-level", "OTA metadata"))
    if timestamp <= 0 or sdk <= 0:
        raise ValueError("OTA timestamp and SDK level must be positive")
    patch = require(metadata, "post-security-patch-level", "OTA metadata")
    dt.date.fromisoformat(patch)
    return {
        "timestamp": timestamp,
        "sdk": sdk,
        "patch": patch,
        "property_files": require(metadata, "ota-property-files", "OTA metadata"),
        "post_build": post_build,
    }


def validate_build_properties(
    product_properties: dict[str, str],
    system_properties: dict[str, str],
    ota_metadata: dict[str, object],
    channel: Channel,
) -> None:
    expected = {
        "ro.lineage.device": channel.device,
        "ro.lineage.releasetype": RELEASE_TYPE,
        "ro.lineage.build.version": LINEAGE_VERSION,
    }
    for key, value in expected.items():
        if require(product_properties, key, "target-files PRODUCT build.prop") != value:
            raise ValueError(f"target-files {key} must equal {value}")
    fingerprint = require(system_properties, "ro.build.fingerprint", "target-files SYSTEM build.prop")
    if fingerprint != ota_metadata["post_build"]:
        raise ValueError("target-files fingerprint does not match OTA post-build")
    try:
        target_timestamp = int(require(system_properties, "ro.build.date.utc", "target-files SYSTEM build.prop"))
    except ValueError as error:
        raise ValueError("target-files timestamp is not an integer") from error
    if target_timestamp != ota_metadata["timestamp"]:
        raise ValueError("target-files timestamp does not match OTA post-timestamp")
    # Builds made before build types existed carry no property; they are vanilla.
    build_type = system_properties.get("ro.yrrp.build.type", VANILLA.type)
    if build_type != channel.type:
        raise ValueError(f"target-files ro.yrrp.build.type is {build_type}, channel needs {channel.type}")


def artifact_record(path: Path, role: str) -> dict[str, object]:
    return {
        "filename": path.name,
        "role": role,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


@dataclass(frozen=True)
class Release:
    build_id: str
    ota: Path
    target_files: Path
    public_base_url: str
    base_image_digest: str
    ota_metadata: dict[str, object]
    incremental: dict[str, Any] | None
    channel: Channel


def validate_incremental(
    incremental: Path, build_id: str, full: dict[str, object], channel: Channel
) -> dict[str, Any]:
    meta = load_meta(incremental, build_id, channel=channel)
    source = "incremental OTA metadata"
    with open_unique_zip(incremental) as archive:
        metadata = read_metadata(archive, source)
    if require(metadata, "ota-type", source) != "AB":
        raise ValueError("incremental OTA metadata must declare ota-type=AB")
    if require(metadata, "post-build", source) != full["post_build"]:
        raise ValueError("incremental post-build does not match full OTA")
    if int(require(metadata, "post-timestamp", source)) != full["timestamp"]:
        raise ValueError("incremental post-timestamp does not match full OTA")
    if require(metadata, "pre-build-incremental", source) != meta["source_incremental"]:
        raise ValueError("incremental pre-build-incremental does not match meta")
    return {**meta, "property_files": require(metadata, "ota-property-files", source)}


def updater_entry(record: dict[str, Any], url: str, ota_metadata: dict[str, object], property_files: str) -> list[dict[str, Any]]:
    return [
        {
            "datetime": ota_metadata["timestamp"],
            "files": [
                {
                    "filename": record["filename"],
                    "os_patch_level": ota_metadata["patch"],
                    "os_sdk_level": ota_metadata["sdk"],
                    "ota_property_files": property_files,
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "url": url,
                }
            ],
            "type": RELEASE_TYPE,
            "version": LINEAGE_VERSION,
        }
    ]


def release_manifest(release: Release, artifacts: list[dict[str, object]], ota_url: str) -> dict[str, Any]:
    incremental = None
    if release.incremental:
        keys = ("filename", "sha256", "size", "source_build_id", "source_incremental", "source_target_files_sha256")
        incremental = {key: release.incremental[key] for key in keys}
    return {
        "artifacts": artifacts,
        "base_image": release.base_image_digest,
        "build_id": release.build_id,
        "channel": release.channel.name,
        "device": release.channel.device,
        "incremental": incremental,
        "lineage_version": LINEAGE_VERSION,
        "ota_timestamp": release.ota_metadata["timestamp"],
        "ota_url": ota_url,
        "patch_level": release.ota_metadata["patch"],
        "project": "YRRP",
        "release_type": RELEASE_TYPE,
        "schema": RELEASE_SCHEMA,
        "sdk_level": release.ota_metadata["sdk"],
        "target_files": {"filename": release.target_files.name, "sha256": sha256(release.target_files)},
    }


def write_updater_entries(rootfs: Path, release: Release, ota_record: dict[str, object], base_url: str) -> str:
    """Write the full and optional incremental updater JSON; return the full OTA URL."""
    channel = release.channel
    ota_url = f"{base_url}/{release.ota.name}"
    full_entry = updater_entry(ota_record, ota_url, release.ota_metadata, str(release.ota_metadata["property_files"]))
    write_json(rootfs / channel.updates_full_path, full_entry)
    if release.incremental:
        incremental = release.incremental
        entry = updater_entry(
            incremental, f"{base_url}/{incremental['filename']}", release.ota_metadata, incremental["property_files"]
        )
        write_json(rootfs / channel.updates_incremental_path(incremental["source_incremental"]), entry)
    return ota_url


def generate_metadata(staging: Path, release_dir: Path, release: Release) -> None:
    base_url = f"{release.public_base_url}/{release.channel.install_dir(release.build_id)}"
    ota_record = artifact_record(release_dir / release.ota.name, "ota")
    artifacts = [ota_record]
    if release.incremental:
        artifacts.append(artifact_record(release_dir / release.incremental["filename"], "incremental-ota"))
    artifacts.extend(artifact_record(release_dir / name, name.removesuffix(".img")) for name in IMAGE_NAMES)
    ota_url = write_updater_entries(staging / "rootfs", release, ota_record, base_url)
    write_json(release_dir / "release.json", release_manifest(release, artifacts, ota_url))
    with (release_dir / "SHA256SUMS.txt").open("w", encoding="utf-8") as output:
        for artifact in artifacts:
            output.write(f"{artifact['sha256']}  {artifact['filename']}\n")


def validate_inputs(ota: Path, build_id: str, base_image_digest: str, channel: Channel) -> None:
    if not BUILD_ID_PATTERN.fullmatch(build_id):
        raise ValueError("build ID must match YYYYMMDD-HHMMSS")
    if ota.name != channel.full_ota_name(build_id):
        raise ValueError("signed OTA filename does not match channel, version, and build ID")
    if not DIGEST_PATTERN.fullmatch(base_image_digest):
        raise ValueError("base image must be pinned by sha256 digest")


def reset_output(output: Path) -> None:
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError("output directory must be absent or empty")
        output.rmdir()


def stage_full_release(ota: Path, target_files: Path, release_dir: Path, channel: Channel) -> dict[str, object]:
    with open_unique_zip(ota) as ota_zip, open_unique_zip(target_files) as target_zip:
        metadata = validate_metadata(read_metadata(ota_zip, "OTA metadata"))
        product_properties = parse_properties(
            target_zip.read("PRODUCT/etc/build.prop"), "target-files PRODUCT build.prop"
        )
        validate_build_properties(product_properties, read_system_properties(target_zip), metadata, channel)
        shutil.copyfile(ota, release_dir / ota.name)
        for image in IMAGE_NAMES:
            copy_member(target_zip, f"IMAGES/{image}", release_dir / image)
    return metadata


def check_release_tree(
    staging: Path, build_id: str, ota_name: str, incremental: dict[str, Any] | None, channel: Channel
) -> None:
    rootfs = Path("rootfs")
    install = rootfs / channel.install_dir(build_id)
    names = [ota_name, *IMAGE_NAMES, "SHA256SUMS.txt", "release.json"]
    allowed = {rootfs / channel.updates_full_path}
    if incremental:
        names.append(incremental["filename"])
        allowed.add(rootfs / channel.updates_incremental_path(incremental["source_incremental"]))
    allowed.update(install / name for name in names)
    actual = {path.relative_to(staging) for path in staging.rglob("*") if path.is_file()}
    if actual != allowed:
        raise ValueError(f"release tree differs from expected: {sorted(map(str, actual ^ allowed))}")


def prepare_release(
    *,
    ota: Path,
    target_files: Path,
    build_id: str,
    public_base_url: str,
    base_image_digest: str,
    output: Path,
    incremental: Path | None = None,
    channel: Channel = VANILLA,
) -> Path:
    ota, target_files, output = Path(ota), Path(target_files), Path(output)
    incremental = Path(incremental) if incremental is not None else None
    validate_inputs(ota, build_id, base_image_digest, channel)
    public_base_url = validate_public_base_url(public_base_url)
    reset_output(output)
    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    try:
        release_dir = staging / "rootfs" / channel.install_dir(build_id)
        release_dir.mkdir(parents=True)
        metadata = stage_full_release(ota, target_files, release_dir, channel)
        incremental_record = None
        if incremental is not None:
            incremental_record = validate_incremental(incremental, build_id, metadata, channel)
            shutil.copyfile(incremental, release_dir / incremental.name)
        release = Release(
            build_id, ota, target_files, public_base_url, base_image_digest, metadata, incremental_record, channel
        )
        generate_metadata(staging, release_dir, release)
        check_release_tree(staging, build_id, ota.name, incremental_record, channel)
        staging.rename(output)
        return output
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare one latest-only YRRP OTA release context")
    parser.add_argument("--ota", type=Path, required=True)
    parser.add_argument("--target-files", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--base-image-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--incremental", type=Path)
    parser.add_argument("--channel", default=VANILLA.name)
    arguments = parser.parse_args()
    result = prepare_release(
        ota=arguments.ota,
        target_files=arguments.target_files,
        build_id=arguments.build_id,
        public_base_url=arguments.public_base_url,
        base_image_digest=arguments.base_image_digest,
        output=arguments.output,
        incremental=arguments.incremental,
        channel=Channel.parse(arguments.channel),
    )
    print(result)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import urllib.parse
import uuid
import zipfile
from pathlib import Path

DEVICE = "salami"
LINEAGE_VERSION = "23.2"
RELEASE_TYPE = "UNOFFICIAL"
BUILD_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}$")
DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
IMAGE_NAMES = (
    "boot.img",
    "dtbo.img",
    "init_boot.img",
    "vbmeta.img",
    "vendor_boot.img",
    "recovery.img",
)


def parse_properties(data: bytes, source: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"invalid property in {source}: {stripped}")
        key, value = stripped.split("=", 1)
        if key in result:
            raise ValueError(f"duplicate property in {source}: {key}")
        result[key] = value
    return result


def open_unique_zip(path: Path) -> zipfile.ZipFile:
    if not path.is_file() or path.stat().st_size == 0 or not zipfile.is_zipfile(path):
        raise ValueError(f"required ZIP missing, empty, or invalid: {path}")
    archive = zipfile.ZipFile(path)
    names = archive.namelist()
    if len(names) != len(set(names)):
        archive.close()
        raise ValueError(f"duplicate ZIP member in {path}")
    return archive


def require(properties: dict[str, str], key: str, source: str) -> str:
    value = properties.get(key, "").strip()
    if not value:
        raise ValueError(f"missing {key} in {source}")
    return value


def validate_public_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("public base URL must use absolute HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("public base URL must not contain credentials, query, or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("public base URL must not contain a path")
    return value.rstrip("/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
) -> None:
    expected = {
        "ro.lineage.device": DEVICE,
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


def artifact_record(path: Path, role: str) -> dict[str, object]:
    return {
        "filename": path.name,
        "role": role,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def generate_metadata(
    release_dir: Path,
    ota: Path,
    target_files: Path,
    build_id: str,
    public_base_url: str,
    base_image_digest: str,
    ota_metadata: dict[str, object],
) -> None:
    ota_copy = release_dir / ota.name
    artifacts = [artifact_record(ota_copy, "ota")]
    artifacts.extend(artifact_record(release_dir / name, name.removesuffix(".img")) for name in IMAGE_NAMES)
    ota_url = f"{public_base_url}/install/{DEVICE}/{build_id}/{ota.name}"
    ota_file = artifacts[0]
    update = {
        "datetime": ota_metadata["timestamp"],
        "files": [
            {
                "filename": ota.name,
                "os_patch_level": ota_metadata["patch"],
                "os_sdk_level": ota_metadata["sdk"],
                "ota_property_files": ota_metadata["property_files"],
                "sha256": ota_file["sha256"],
                "size": ota_file["size"],
                "url": ota_url,
            }
        ],
        "type": RELEASE_TYPE,
        "version": LINEAGE_VERSION,
    }
    write_json(release_dir.parents[2] / "updates" / f"{DEVICE}.json", [update])
    release = {
        "artifacts": artifacts,
        "base_image": base_image_digest,
        "build_id": build_id,
        "device": DEVICE,
        "lineage_version": LINEAGE_VERSION,
        "ota_timestamp": ota_metadata["timestamp"],
        "ota_url": ota_url,
        "patch_level": ota_metadata["patch"],
        "project": "YRRP",
        "release_type": RELEASE_TYPE,
        "schema": 1,
        "sdk_level": ota_metadata["sdk"],
        "target_files": {"filename": target_files.name, "sha256": sha256(target_files)},
    }
    write_json(release_dir / "release.json", release)
    checksum_order = [ota.name, *IMAGE_NAMES]
    with (release_dir / "SHA256SUMS.txt").open("w", encoding="utf-8") as output:
        for name in checksum_order:
            output.write(f"{sha256(release_dir / name)}  {name}\n")


def prepare_release(
    *,
    ota: Path,
    target_files: Path,
    build_id: str,
    public_base_url: str,
    base_image_digest: str,
    output: Path,
) -> Path:
    ota = Path(ota)
    target_files = Path(target_files)
    output = Path(output)
    if not BUILD_ID_PATTERN.fullmatch(build_id):
        raise ValueError("build ID must match YYYYMMDD-HHMMSS")
    if ota.name != f"lineage-{LINEAGE_VERSION}-{DEVICE}-{build_id}-signed-ota.zip":
        raise ValueError("signed OTA filename does not match device, version, and build ID")
    public_base_url = validate_public_base_url(public_base_url)
    if not DIGEST_PATTERN.fullmatch(base_image_digest):
        raise ValueError("base image must be pinned by sha256 digest")
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError("output directory must be absent or empty")
        output.rmdir()

    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    try:
        release_dir = staging / "rootfs" / "install" / DEVICE / build_id
        updates_dir = staging / "rootfs" / "updates"
        release_dir.mkdir(parents=True)
        updates_dir.mkdir(parents=True)
        with open_unique_zip(ota) as ota_zip, open_unique_zip(target_files) as target_zip:
            metadata = validate_metadata(
                parse_properties(ota_zip.read("META-INF/com/android/metadata"), "OTA metadata")
            )
            product_properties = parse_properties(
                target_zip.read("PRODUCT/etc/build.prop"), "target-files PRODUCT build.prop"
            )
            system_member = next(
                (name for name in ("SYSTEM/etc/build.prop", "SYSTEM/build.prop") if name in target_zip.namelist()),
                None,
            )
            if system_member is None:
                raise ValueError("target-files SYSTEM build.prop is missing")
            system_properties = parse_properties(
                target_zip.read(system_member), "target-files SYSTEM build.prop"
            )
            validate_build_properties(product_properties, system_properties, metadata)
            shutil.copyfile(ota, release_dir / ota.name)
            for image in IMAGE_NAMES:
                copy_member(target_zip, f"IMAGES/{image}", release_dir / image)
        generate_metadata(
            release_dir,
            ota,
            target_files,
            build_id,
            public_base_url,
            base_image_digest,
            metadata,
        )
        allowed = {
            Path("rootfs/updates/salami.json"),
            *(Path("rootfs/install/salami") / build_id / name for name in (ota.name, *IMAGE_NAMES, "SHA256SUMS.txt", "release.json")),
        }
        actual = {path.relative_to(staging) for path in staging.rglob("*") if path.is_file()}
        if actual != allowed:
            raise ValueError(f"release tree contains unexpected files: {sorted(actual - allowed)}")
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
    arguments = parser.parse_args()
    result = prepare_release(
        ota=arguments.ota,
        target_files=arguments.target_files,
        build_id=arguments.build_id,
        public_base_url=arguments.public_base_url,
        base_image_digest=arguments.base_image_digest,
        output=arguments.output,
    )
    print(result)


if __name__ == "__main__":
    main()

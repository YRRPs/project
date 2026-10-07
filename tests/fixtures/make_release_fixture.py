from __future__ import annotations

import hashlib
import json
import warnings
import zipfile
from pathlib import Path

BUILD_ID = "20990101-000000"
OTA_NAME = f"lineage-23.2-salami-{BUILD_ID}-signed-ota.zip"
TARGET_NAME = f"lineage-23.2-salami-{BUILD_ID}-signed-target_files.zip"
IMAGES = (
    "boot.img",
    "dtbo.img",
    "init_boot.img",
    "vbmeta.img",
    "vendor_boot.img",
    "recovery.img",
)

SOURCE_BUILD_ID = "20981231-000000"
SOURCE_INCREMENTAL = "4070822400"
POST_BUILD = "yrpp/salami/salami:16/TEST/4070908800:userdebug/release-keys"
INCREMENTAL_NAME = f"lineage-23.2-salami-{SOURCE_BUILD_ID}-to-{BUILD_ID}-signed-incremental-ota.zip"


def create_fixture(
    root: Path,
    *,
    post_build_suffix: str = "release-keys",
    missing_image: str | None = None,
    duplicate_member: bool = False,
    target_fingerprint: str | None = None,
    target_timestamp: int = 4070908800,
    system_prop_path: str = "SYSTEM/etc/build.prop",
    build_id: str = BUILD_ID,
    target_incremental: str | None = None,
) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    ota = root / f"lineage-23.2-salami-{build_id}-signed-ota.zip"
    target = root / f"lineage-23.2-salami-{build_id}-signed-target_files.zip"
    post_build = f"yrpp/salami/salami:16/TEST/4070908800:userdebug/{post_build_suffix}"
    target_fingerprint = target_fingerprint or post_build
    metadata = "\n".join(
        (
            f"post-build={post_build}",
            "post-timestamp=4070908800",
            "post-sdk-level=36",
            "post-security-patch-level=2099-01-01",
            "ota-type=AB",
            "ota-property-files=payload.bin:1:2,metadata:3:4",
        )
    )
    with zipfile.ZipFile(ota, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("META-INF/com/android/metadata", metadata + "\n")
        archive.writestr("payload.bin", b"synthetic signed payload")
        if duplicate_member:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("payload.bin", b"duplicate")

    build_prop = "\n".join(
        (
            "ro.product.device=salami",
            "ro.lineage.device=salami",
            "ro.lineage.releasetype=UNOFFICIAL",
            "ro.lineage.build.version=23.2",
        )
    )
    system_build_prop = "\n".join(
        (
            f"ro.build.fingerprint={target_fingerprint}",
            f"ro.build.date.utc={target_timestamp}",
            f"ro.build.version.incremental={target_incremental or target_timestamp}",
        )
    )
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("PRODUCT/etc/build.prop", build_prop + "\n")
        archive.writestr(system_prop_path, system_build_prop + "\n")
        for index, image in enumerate(IMAGES):
            if image != missing_image:
                archive.writestr(f"IMAGES/{image}", f"synthetic-{index}-{image}\n")
        archive.writestr("PREBUILT_IMAGES/dtbo.img", b"must-not-be-used")
    return ota, target


def create_incremental_fixture(
    root: Path,
    *,
    pre_incremental: str = SOURCE_INCREMENTAL,
    post_timestamp: int = 4070908800,
    post_build: str = POST_BUILD,
    name: str = INCREMENTAL_NAME,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    metadata = "\n".join(
        (
            "ota-type=AB",
            f"post-build={post_build}",
            f"post-timestamp={post_timestamp}",
            f"pre-build-incremental={pre_incremental}",
            "ota-property-files=payload.bin:5:6,metadata:7:8",
        )
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("META-INF/com/android/metadata", metadata + "\n")
        archive.writestr("META-INF/com/android/otacert", b"synthetic-certificate")
        archive.writestr("payload.bin", b"synthetic incremental payload")
    return path


def write_incremental_meta(
    incremental: Path,
    *,
    source_incremental: str = SOURCE_INCREMENTAL,
    digest: str | None = None,
) -> Path:
    meta = {
        "filename": incremental.name,
        "sha256": digest or hashlib.sha256(incremental.read_bytes()).hexdigest(),
        "size": incremental.stat().st_size,
        "source_build_id": SOURCE_BUILD_ID,
        "source_incremental": source_incremental,
        "source_target_files_sha256": "c" * 64,
    }
    path = incremental.with_name(incremental.name + ".json")
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

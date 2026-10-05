from __future__ import annotations

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


def create_fixture(
    root: Path,
    *,
    post_build_suffix: str = "release-keys",
    missing_image: str | None = None,
    duplicate_member: bool = False,
    target_fingerprint: str | None = None,
    target_timestamp: int = 4070908800,
    system_prop_path: str = "SYSTEM/etc/build.prop",
) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    ota = root / OTA_NAME
    target = root / TARGET_NAME
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

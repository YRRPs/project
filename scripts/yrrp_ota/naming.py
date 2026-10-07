"""Release identity: device, version, build-ID shape, and artifact names."""
from __future__ import annotations

import re

DEVICE = "salami"
LINEAGE_VERSION = "23.2"
BUILD_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}$")
INCREMENTAL_PATTERN = re.compile(r"^[0-9]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _prefix() -> str:
    return f"lineage-{LINEAGE_VERSION}-{DEVICE}"


def full_ota_name(build_id: str) -> str:
    return f"{_prefix()}-{build_id}-signed-ota.zip"


def incremental_ota_name(source_build_id: str, target_build_id: str) -> str:
    return f"{_prefix()}-{source_build_id}-to-{target_build_id}-signed-incremental-ota.zip"

"""Release channels: one (device, type) pair, and every name, path, and label derived from it."""
from __future__ import annotations

import re
from dataclasses import dataclass

LINEAGE_VERSION = "23.2"
BUILD_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}$")
VANILLA_TYPE = "vanilla"
ALLOWED = frozenset({("salami", "vanilla"), ("salami", "gapps")})
LABEL_PREFIX = "io.yrrp.ota.channel."
LABEL_SUFFIX = ".build-id"
LEGACY_DEVICE_LABEL = "io.yrrp.ota.device"
LEGACY_BUILD_LABEL = "io.yrrp.ota.build-id"


@dataclass(frozen=True)
class Channel:
    device: str
    type: str

    def __post_init__(self) -> None:
        if (self.device, self.type) not in ALLOWED:
            allowed = ", ".join(sorted(f"{d}/{t}" for d, t in ALLOWED))
            raise ValueError(f"unknown channel {self.device}/{self.type}; allowed: {allowed}")

    @classmethod
    def parse(cls, value: str) -> "Channel":
        parts = value.split("/")
        if len(parts) != 2:
            raise ValueError(f"channel must be DEVICE/TYPE: {value}")
        return cls(parts[0], parts[1])

    @property
    def name(self) -> str:
        return f"{self.device}/{self.type}"

    @property
    def is_vanilla(self) -> bool:
        return self.type == VANILLA_TYPE

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}{self.device}.{self.type}{LABEL_SUFFIX}"

    @property
    def _prefix(self) -> str:
        base = f"lineage-{LINEAGE_VERSION}-{self.device}"
        return base if self.is_vanilla else f"{base}-{self.type}"

    def target_files_name(self, build_id: str) -> str:
        return f"{self._prefix}-{build_id}-signed-target_files.zip"

    def full_ota_name(self, build_id: str) -> str:
        return f"{self._prefix}-{build_id}-signed-ota.zip"

    def incremental_ota_name(self, source_build_id: str, target_build_id: str) -> str:
        return f"{self._prefix}-{source_build_id}-to-{target_build_id}-signed-incremental-ota.zip"

    def checksums_name(self, build_id: str) -> str:
        return f"{self._prefix}-{build_id}-SHA256SUMS.txt"

    @property
    def updates_full_path(self) -> str:
        if self.is_vanilla:
            return f"updates/{self.device}.json"
        return f"updates/{self.device}/{self.type}.json"

    @property
    def updates_incremental_dir(self) -> str:
        if self.is_vanilla:
            return f"updates/{self.device}"
        return f"updates/{self.device}/{self.type}"

    def updates_incremental_path(self, source_incremental: str) -> str:
        return f"{self.updates_incremental_dir}/{source_incremental}.json"

    def install_dir(self, build_id: str) -> str:
        if self.is_vanilla:
            return f"install/{self.device}/{build_id}"
        return f"install/{self.device}/{self.type}/{build_id}"


VANILLA = Channel("salami", "vanilla")
GAPPS = Channel("salami", "gapps")


def _require_build_id(value: str, label: str) -> str:
    if not BUILD_ID_PATTERN.fullmatch(value):
        raise ValueError(f"label {label} has invalid build ID: {value!r}")
    return value


def live_channels(labels: dict[str, str]) -> dict[Channel, str]:
    """Map each channel served by a container to its live build ID."""
    result: dict[Channel, str] = {}
    for key, value in labels.items():
        if key.startswith(LABEL_PREFIX) and key.endswith(LABEL_SUFFIX):
            device, _, kind = key[len(LABEL_PREFIX) : -len(LABEL_SUFFIX)].partition(".")
            result[Channel(device, kind)] = _require_build_id(value, key)
    if not result and labels.get(LEGACY_DEVICE_LABEL) == VANILLA.device and LEGACY_BUILD_LABEL in labels:
        result[VANILLA] = _require_build_id(labels[LEGACY_BUILD_LABEL], LEGACY_BUILD_LABEL)
    return result

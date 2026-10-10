"""Release channels: one (device, type) pair, and every name, path, and label derived from it."""
from __future__ import annotations

import re
from dataclasses import dataclass

LINEAGE_VERSION = "23.2"
BUILD_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}$")
INCREMENTAL_PATTERN = re.compile(r"^[0-9]+$")
ALLOWLIST_TOKEN_PATTERN = re.compile(r"^[a-z0-9]+$")
VANILLA_TYPE = "vanilla"
ALLOWED = frozenset({("salami", "vanilla"), ("salami", "gapps")})
LABEL_PREFIX = "io.yrrp.ota.channel."
LABEL_SUFFIX = ".build-id"
LEGACY_DEVICE_LABEL = "io.yrrp.ota.device"
LEGACY_BUILD_LABEL = "io.yrrp.ota.build-id"


def validate_allowlist(allowed: frozenset[tuple[str, str]]) -> None:
    """Refuse allowlist entries that could alter a name, path, or label."""
    for device, channel_type in allowed:
        for token in (device, channel_type):
            if not ALLOWLIST_TOKEN_PATTERN.fullmatch(token):
                raise RuntimeError(f"invalid channel allowlist token {token!r} in {device}/{channel_type}")


validate_allowlist(ALLOWED)


def require_build_id(value: str, what: str) -> str:
    if not BUILD_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{what} has invalid build ID: {value!r}")
    return value


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
        return f"{self._prefix}-{require_build_id(build_id, 'build ID')}-signed-target_files.zip"

    def full_ota_name(self, build_id: str) -> str:
        return f"{self._prefix}-{require_build_id(build_id, 'build ID')}-signed-ota.zip"

    def incremental_ota_name(self, source_build_id: str, target_build_id: str) -> str:
        source = require_build_id(source_build_id, "source build ID")
        target = require_build_id(target_build_id, "target build ID")
        return f"{self._prefix}-{source}-to-{target}-signed-incremental-ota.zip"

    def checksums_name(self, build_id: str) -> str:
        return f"{self._prefix}-{require_build_id(build_id, 'build ID')}-SHA256SUMS.txt"

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
        """source_incremental is the source build's numeric ro.build.version.incremental."""
        if not INCREMENTAL_PATTERN.fullmatch(source_incremental):
            raise ValueError(f"invalid source incremental: {source_incremental!r}")
        return f"{self.updates_incremental_dir}/{source_incremental}.json"

    def install_dir(self, build_id: str) -> str:
        require_build_id(build_id, "build ID")
        if self.is_vanilla:
            return f"install/{self.device}/{build_id}"
        return f"install/{self.device}/{self.type}/{build_id}"


VANILLA = Channel("salami", "vanilla")
GAPPS = Channel("salami", "gapps")


def _legacy_vanilla_build(labels: dict[str, str]) -> dict[Channel, str]:
    """Read the pre-channel device/build-id label pair, which meant salami/vanilla."""
    device = labels.get(LEGACY_DEVICE_LABEL)
    build_id = labels.get(LEGACY_BUILD_LABEL)
    if device is None and build_id is None:
        return {}
    if device is None or build_id is None:
        raise ValueError(f"legacy labels {LEGACY_DEVICE_LABEL} and {LEGACY_BUILD_LABEL} must both be present")
    if device != VANILLA.device:
        raise ValueError(f"label {LEGACY_DEVICE_LABEL} has unsupported device: {device!r}")
    return {VANILLA: require_build_id(build_id, f"label {LEGACY_BUILD_LABEL}")}


def live_channels(labels: dict[str, str]) -> dict[Channel, str]:
    """Map each channel served by a container to its live build ID."""
    result: dict[Channel, str] = {}
    for key, value in labels.items():
        if key.startswith(LABEL_PREFIX) and key.endswith(LABEL_SUFFIX):
            device, _, channel_type = key[len(LABEL_PREFIX) : -len(LABEL_SUFFIX)].partition(".")
            result[Channel(device, channel_type)] = require_build_id(value, f"label {key}")
    return result or _legacy_vanilla_build(labels)

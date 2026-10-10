"""Receipt rules that depend on the released channel: names, routes, carried channels."""
from __future__ import annotations

import re
from typing import Any

from yrrp_ota.channel import BUILD_ID_PATTERN, VANILLA, Channel

INCREMENTAL_SUFFIX = "-signed-incremental-ota.zip"
INCREMENTAL_PATTERN = re.compile(
    r".+-(?P<source>[0-9]{8}-[0-9]{6})-to-(?P<target>[0-9]{8}-[0-9]{6})"
    + re.escape(INCREMENTAL_SUFFIX)
)
CARRIED_LABEL = "deployment_public_checks.carried_channels"
CARRIED_FIELDS = frozenset({"channel", "build_id", "routes_unchanged"})
STALE_FALLBACK_INCREMENTAL = "1"


def release_channel(identity: dict[str, Any]) -> Channel:
    """The released channel; receipts written before channels existed mean salami/vanilla."""
    value = identity.get("channel", VANILLA.name)
    if not isinstance(value, str):
        raise TypeError("release_identity.channel must be a string")
    return Channel.parse(value)


def receipt_stem(channel: Channel, build_id: str) -> str:
    return build_id if channel.is_vanilla else f"{channel.type}-{build_id}"


def required_artifact_names(channel: Channel, build_id: str) -> set[str]:
    return {
        channel.target_files_name(build_id),
        channel.full_ota_name(build_id),
        channel.checksums_name(build_id),
    }


def incremental_source(name: str, target_build_id: str, channel: Channel) -> str:
    """Return the source build ID of a channel incremental that targets target_build_id."""
    match = INCREMENTAL_PATTERN.fullmatch(name)
    if not match or match["target"] != target_build_id or match["source"] == target_build_id:
        raise ValueError("incremental artifact source/target relationship is invalid")
    if name != channel.incremental_ota_name(match["source"], target_build_id):
        raise ValueError(f"incremental artifact name does not belong to channel {channel.name}")
    return match["source"]


def expected_routes(channel: Channel, build_id: str, incremental_name: str | None) -> dict[str, str]:
    install = f"/{channel.install_dir(build_id)}"
    paths = {
        "healthz": "/healthz",
        "updates metadata": f"/{channel.updates_full_path}",
        "stale fallback": f"/{channel.updates_incremental_path(STALE_FALLBACK_INCREMENTAL)}",
        "install listing": f"{install}/",
        "full OTA range": f"{install}/{channel.full_ota_name(build_id)}",
    }
    if incremental_name is not None:
        paths["incremental OTA range"] = f"{install}/{incremental_name}"
    return paths


def validate_carried_channels(value: Any, released: Channel) -> None:
    """Each carried channel is another allowed channel, named once, with a valid live build ID."""
    if not isinstance(value, list):
        raise TypeError(f"{CARRIED_LABEL} must be a JSON array")
    seen: set[Channel] = set()
    for index, entry in enumerate(value):
        label = f"{CARRIED_LABEL}[{index}]"
        if not isinstance(entry, dict) or set(entry) != CARRIED_FIELDS:
            raise ValueError(f"{label} must have exactly the fields {sorted(CARRIED_FIELDS)}")
        if not isinstance(entry["channel"], str):
            raise TypeError(f"{label}.channel must be a string")
        channel = Channel.parse(entry["channel"])
        if channel == released:
            raise ValueError(f"{label} repeats the released channel {released.name}")
        if channel in seen:
            raise ValueError(f"duplicate carried channel: {channel.name}")
        seen.add(channel)
        if not isinstance(entry["build_id"], str) or not BUILD_ID_PATTERN.fullmatch(entry["build_id"]):
            raise ValueError(f"{label}.build_id must match YYYYMMDD-HHMMSS")
        if not isinstance(entry["routes_unchanged"], bool):
            raise TypeError(f"{label}.routes_unchanged must be a boolean")


def require_carried_routes_unchanged(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        if entry["routes_unchanged"] is not True:
            raise ValueError(f"carried channel {entry['channel']} {entry['build_id']} routes changed")

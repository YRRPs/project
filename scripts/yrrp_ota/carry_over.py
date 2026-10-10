"""Copy, verify, and route-check the channels a deploy does not release.

Every check fails closed: a carried channel that cannot be copied or proven intact
stops the deploy instead of being dropped or served corrupted.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .archive import sha256 as file_sha256
from .channel import VANILLA, Channel, live_channels

SERVE_ROOT = "/srv/ota"
INTERNAL_URL = "http://127.0.0.1:8080"
CHANNEL_SCHEMA = 3
SUMS_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


class CarryOverError(RuntimeError):
    """A carried channel could not be copied or verified, or its routes changed."""


@dataclass
class CarryResult:
    carried: dict[Channel, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    snapshot: dict[str, str] = field(default_factory=dict)


def _docker(args: list[str], env: Mapping[str, str] | None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", *args], capture_output=True, env=env)


def read_labels(container: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Labels of the live container; a container without labels has none."""
    result = _docker(["inspect", "--format", "{{json .Config.Labels}}", container], env)
    if result.returncode != 0:
        raise CarryOverError(f"cannot inspect labels of {container}: {result.stderr.decode().strip()}")
    try:
        labels = json.loads(result.stdout)
    except ValueError as error:
        raise CarryOverError(f"{container} labels are not valid JSON: {error}") from error
    if labels is None:  # docker prints null for a container without labels
        return {}
    if not isinstance(labels, dict):
        raise CarryOverError(f"{container} labels must be a JSON object")
    return labels


def _live_channels(container: str, env: Mapping[str, str] | None) -> dict[Channel, str]:
    try:
        return live_channels(read_labels(container, env))
    except ValueError as error:
        raise CarryOverError(f"{container} labels: {error}") from error


def _copy(container: str, channel: Channel, path: str, rootfs: Path, env: Mapping[str, str] | None) -> Path:
    destination = rootfs / path
    if destination.is_symlink() or destination.exists():
        raise CarryOverError(f"{channel.name}: {path} already exists in the release context")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _docker(["cp", f"{container}:{SERVE_ROOT}/{path}", str(destination)], env)
    if result.returncode != 0:
        raise CarryOverError(f"{channel.name}: cannot copy {path}: {result.stderr.decode().strip()}")
    # docker cp keeps symlinks and directories; reading through either would verify host files.
    if not stat.S_ISREG(destination.lstat().st_mode):
        raise CarryOverError(f"{channel.name}: {path} in {container} is not a regular file")
    return destination


def _load_json(channel: Channel, path: Path, route: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise CarryOverError(f"{channel.name}: {route} is not valid JSON: {error}") from error


def _check_identity(channel: Channel, build_id: str, release: dict[str, Any]) -> None:
    if release.get("build_id") != build_id:
        raise CarryOverError(f"{channel.name}: release.json build_id {release.get('build_id')!r} != label {build_id}")
    schema = release.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise CarryOverError(f"{channel.name}: release.json schema is not an integer: {schema!r}")
    recorded = release.get("channel")
    if schema < CHANNEL_SCHEMA and channel != VANILLA:
        raise CarryOverError(f"{channel.name}: release.json schema {schema} predates channels; only {VANILLA.name} may use it")
    if schema < CHANNEL_SCHEMA and recorded is None:
        return
    if recorded != channel.name:
        raise CarryOverError(f"{channel.name}: release.json channel is {recorded!r}")


def _require_basename(channel: Channel, name: Any) -> str:
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise CarryOverError(f"{channel.name}: release.json has unsafe artifact filename {name!r}")
    return name


def _artifacts(channel: Channel, release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Artifacts by filename, refusing any filename that is not a plain basename."""
    records = release.get("artifacts")
    if not isinstance(records, list) or not records:
        raise CarryOverError(f"{channel.name}: release.json artifacts must be a non-empty list")
    artifacts: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
            raise CarryOverError(f"{channel.name}: release.json has a malformed artifact: {record!r}")
        name = _require_basename(channel, record.get("filename"))
        if name in artifacts:
            raise CarryOverError(f"{channel.name}: release.json lists artifact {name} twice")
        artifacts[name] = record
    return artifacts


def _artifact_with_role(channel: Channel, artifacts: dict[str, dict[str, Any]], role: str) -> dict[str, Any]:
    matches = [record for record in artifacts.values() if record.get("role") == role]
    if len(matches) != 1:
        raise CarryOverError(f"{channel.name}: release.json needs exactly one {role} artifact, has {len(matches)}")
    return matches[0]


def _routes(
    channel: Channel, build_id: str, release: dict[str, Any], artifacts: dict[str, dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    """Each updater route this release serves, with the artifact its entry must point at."""
    ota = _artifact_with_role(channel, artifacts, "ota")
    if ota["filename"] != channel.full_ota_name(build_id):
        raise CarryOverError(f"{channel.name}: release.json ota artifact is {ota['filename']}")
    routes = [(channel.updates_full_path, ota)]
    incremental = release.get("incremental")
    if incremental is None:
        return routes
    if not isinstance(incremental, dict):
        raise CarryOverError(f"{channel.name}: release.json incremental must be an object or null")
    try:
        route = channel.updates_incremental_path(incremental.get("source_incremental"))
    except ValueError as error:
        raise CarryOverError(f"{channel.name}: release.json incremental: {error}") from error
    record = _artifact_with_role(channel, artifacts, "incremental-ota")
    if incremental.get("filename") != record["filename"]:
        raise CarryOverError(f"{channel.name}: release.json incremental filename does not match its artifact")
    return [*routes, (route, record)]


def _read_sums(channel: Channel, path: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SUMS_LINE.fullmatch(line)
        if not match:
            raise CarryOverError(f"{channel.name}: SHA256SUMS.txt has a malformed line: {line!r}")
        digest, name = match.groups()
        if name in sums:
            raise CarryOverError(f"{channel.name}: SHA256SUMS.txt lists {name} twice")
        sums[name] = digest
    return sums


def _verify_hashes(channel: Channel, install: Path, artifacts: dict[str, dict[str, Any]]) -> None:
    sums = _read_sums(channel, install / "SHA256SUMS.txt")
    if set(sums) != set(artifacts):
        differing = sorted(set(sums) ^ set(artifacts))
        raise CarryOverError(f"{channel.name}: SHA256SUMS.txt and release.json artifacts differ: {differing}")
    for name, record in artifacts.items():
        actual = file_sha256(install / name)
        if actual != record["sha256"] or actual != sums[name]:
            raise CarryOverError(f"{channel.name}: SHA-256 mismatch for {install.name}/{name}")


def _updater_file(channel: Channel, route: str, entry: Any) -> dict[str, Any]:
    """The first file record of an updater JSON entry list, as prepare-ota-release.py writes it."""
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        files = entry[0].get("files")
        if isinstance(files, list) and files and isinstance(files[0], dict):
            return files[0]
    raise CarryOverError(f"{channel.name}: {route} is not an updater entry list")


def _check_entry(channel: Channel, route: str, path: Path, install_dir: str, artifact: dict[str, Any]) -> None:
    """Refuse an updater entry that points at anything but this build's artifact."""
    record = _updater_file(channel, route, _load_json(channel, path, route))
    url = record.get("url")
    expected = f"/{install_dir}/{artifact['filename']}"
    if not isinstance(url, str) or not urllib.parse.urlsplit(url).path.endswith(expected):
        raise CarryOverError(f"{channel.name}: {route} url {url!r} does not point at {expected}")
    if record.get("filename") != artifact["filename"] or record.get("sha256") != artifact["sha256"]:
        raise CarryOverError(f"{channel.name}: {route} filename or sha256 does not match {artifact['filename']}")


def _carry_channel(container: str, channel: Channel, build_id: str, rootfs: Path, env) -> list[str]:
    """Copy one channel's release and updater routes, verify them, and return the routes."""
    install_dir = channel.install_dir(build_id)

    def copy(path: str) -> Path:
        return _copy(container, channel, path, rootfs, env)

    release = _load_json(channel, copy(f"{install_dir}/release.json"), f"{install_dir}/release.json")
    if not isinstance(release, dict):
        raise CarryOverError(f"{channel.name}: {install_dir}/release.json is not an object")
    _check_identity(channel, build_id, release)
    artifacts = _artifacts(channel, release)
    routes = _routes(channel, build_id, release, artifacts)
    copy(f"{install_dir}/SHA256SUMS.txt")
    for name in artifacts:
        copy(f"{install_dir}/{name}")
    _verify_hashes(channel, rootfs / install_dir, artifacts)
    for route, artifact in routes:
        _check_entry(channel, route, copy(route), install_dir, artifact)
    return [route for route, _ in routes]


def carry_over(
    container: str, releasing: Channel, context: Path, *, env: Mapping[str, str] | None = None
) -> CarryResult:
    """Copy every live channel except |releasing| into |context|/rootfs and verify it."""
    result = CarryResult()
    rootfs = context / "rootfs"
    live = _live_channels(container, env)
    for channel, build_id in sorted(live.items(), key=lambda item: item[0].name):
        if channel == releasing:
            continue
        for route in _carry_channel(container, channel, build_id, rootfs, env):
            result.snapshot[route] = file_sha256(rootfs / route)
        result.carried[channel] = build_id
        result.labels[channel.label] = build_id
    return result


def verify_routes(container: str, snapshot: Mapping[str, str], *, env: Mapping[str, str] | None = None) -> None:
    """Fail unless the container serves every snapshot route with a byte-identical body."""
    for route, expected in snapshot.items():
        result = _docker(["exec", container, "wget", "-q", "-O", "-", f"{INTERNAL_URL}/{route}"], env)
        if result.returncode != 0:
            raise CarryOverError(f"carried route /{route} is not served")
        if hashlib.sha256(result.stdout).hexdigest() != expected:
            raise CarryOverError(f"carried route /{route} changed")

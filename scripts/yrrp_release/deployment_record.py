from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yrrp_ota.channel import BUILD_ID_PATTERN, Channel
from yrrp_release.launcher import validate_sha, validate_sha256

RECORD_KEYS = frozenset(
    {
        "schema_version",
        "channel",
        "build_id",
        "created_at",
        "deployer_project_sha",
        "full_ota",
        "target_files",
        "checksums",
        "incremental",
    }
)
ARTIFACT_KEYS = frozenset({"name", "size", "sha256"})
CHECKSUM_KEYS = frozenset({"name", "sha256"})
INCREMENTAL_KEYS = frozenset({"name", "size", "sha256", "source_incremental"})
SOURCE_INCREMENTAL_PATTERN = re.compile(r"[0-9]+")


def deployment_record_from_receipt(
    receipt: object, channel: Channel, build_id: str
) -> dict[str, object]:
    document = _mapping(receipt, "receipt")
    identity = _mapping(document.get("release_identity"), "receipt.release_identity")
    if identity.get("channel") != channel.name or identity.get("build_id") != build_id:
        raise ValueError("receipt identity does not match rollback request")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise TypeError("receipt.artifacts must be a list")
    expected = {
        channel.full_ota_name(build_id),
        channel.target_files_name(build_id),
        channel.checksums_name(build_id),
    }
    normalized: dict[str, dict[str, object]] = {}
    incremental_count = 0
    for index, value in enumerate(artifacts):
        artifact = _mapping(value, f"receipt.artifacts[{index}]")
        name = _text(artifact.get("name"), f"receipt.artifacts[{index}].name")
        if Path(name).name != name:
            raise ValueError("receipt artifact name must be a basename")
        is_incremental = name.endswith("-signed-incremental-ota.zip")
        if name not in expected and not is_incremental:
            continue
        if is_incremental:
            _incremental_source_build(channel, name, build_id)
            incremental_count += 1
            if incremental_count > 1:
                raise ValueError("receipt contains multiple incremental artifacts")
        if artifact.get("checksum_verified") is not True:
            raise ValueError("receipt artifact checksum was not verified")
        size = artifact.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("receipt artifact size must be positive")
        sha256 = validate_sha256(
            _text(artifact.get("sha256"), f"receipt.artifacts[{index}].sha256"),
            "receipt artifact SHA-256",
        )
        if name in normalized:
            raise ValueError(f"duplicate receipt artifact: {name}")
        normalized[name] = {"size": size, "sha256": sha256}
    missing = sorted(expected - set(normalized))
    if missing:
        raise ValueError("receipt is missing rollback artifacts: " + ", ".join(missing))
    return {
        "channel": channel.name,
        "build_id": build_id,
        "artifacts": dict(sorted(normalized.items())),
    }


def build_deployment_record(
    *,
    channel: Channel,
    build_id: str,
    signed_root: Path,
    ota_name: str,
    target_files_name: str,
    incremental_name: str | None,
    created_at: datetime,
    deployer_project_sha: str | None,
) -> dict[str, object]:
    expected_ota = channel.full_ota_name(build_id)
    expected_target = channel.target_files_name(build_id)
    if ota_name != expected_ota or target_files_name != expected_target:
        raise ValueError("deployment artifact names do not match channel and build ID")
    record: dict[str, object] = {
        "schema_version": 1,
        "channel": channel.name,
        "build_id": build_id,
        "created_at": _format_time(created_at),
        "deployer_project_sha": (
            validate_sha(deployer_project_sha, "deployer project SHA")
            if deployer_project_sha is not None
            else None
        ),
        "full_ota": _artifact_record(signed_root, ota_name),
        "target_files": _artifact_record(signed_root, target_files_name),
        "checksums": _checksum_record(signed_root, channel.checksums_name(build_id)),
        "incremental": None,
    }
    if incremental_name is not None:
        source_build = _incremental_source_build(channel, incremental_name, build_id)
        metadata_path = signed_root / f"{incremental_name}.json"
        metadata = _read_json_object(metadata_path, "incremental metadata")
        source_incremental = metadata.get("source_incremental")
        if not isinstance(source_incremental, str) or not SOURCE_INCREMENTAL_PATTERN.fullmatch(
            source_incremental
        ):
            raise ValueError("incremental metadata has invalid source_incremental")
        incremental = _artifact_record(signed_root, incremental_name)
        incremental["source_incremental"] = source_incremental
        record["incremental"] = incremental
        if source_build >= build_id:
            raise ValueError("incremental source build must precede target build")
    return validate_deployment_record(
        record, expected_channel=channel, expected_build_id=build_id
    )


def validate_deployment_record(
    document: object,
    *,
    expected_channel: Channel,
    expected_build_id: str,
) -> dict[str, object]:
    record = _mapping(document, "deployment record")
    _require_keys(record, RECORD_KEYS, "deployment record")
    if record["schema_version"] != 1:
        raise ValueError("deployment record has unsupported schema version")
    if record["channel"] != expected_channel.name:
        raise ValueError("deployment record channel does not match request")
    if record["build_id"] != expected_build_id or not BUILD_ID_PATTERN.fullmatch(
        expected_build_id
    ):
        raise ValueError("deployment record build ID does not match request")
    _parse_time(record["created_at"], "deployment record.created_at")
    project_sha = record["deployer_project_sha"]
    if project_sha is not None:
        validate_sha(project_sha, "deployer project SHA")
    _validate_artifact(
        record["full_ota"],
        "deployment record.full_ota",
        expected_channel.full_ota_name(expected_build_id),
        ARTIFACT_KEYS,
    )
    _validate_artifact(
        record["target_files"],
        "deployment record.target_files",
        expected_channel.target_files_name(expected_build_id),
        ARTIFACT_KEYS,
    )
    _validate_artifact(
        record["checksums"],
        "deployment record.checksums",
        expected_channel.checksums_name(expected_build_id),
        CHECKSUM_KEYS,
    )
    incremental = record["incremental"]
    if incremental is not None:
        validated_incremental = _validate_artifact(
            incremental,
            "deployment record.incremental",
            None,
            INCREMENTAL_KEYS,
        )
        _incremental_source_build(
            expected_channel, validated_incremental["name"], expected_build_id
        )
        source_incremental = validated_incremental["source_incremental"]
        if not isinstance(source_incremental, str) or not SOURCE_INCREMENTAL_PATTERN.fullmatch(
            source_incremental
        ):
            raise ValueError("deployment record incremental source is invalid")
    return copy.deepcopy(record)


def verify_record_artifacts(
    record: object, signed_root: Path
) -> dict[str, Path]:
    raw = _mapping(record, "deployment record")
    channel = Channel.parse(_text(raw.get("channel"), "deployment record.channel"))
    build_id = _text(raw.get("build_id"), "deployment record.build_id")
    validated = validate_deployment_record(
        raw, expected_channel=channel, expected_build_id=build_id
    )
    paths: dict[str, Path] = {}
    for field in ("full_ota", "target_files", "checksums", "incremental"):
        artifact = validated[field]
        if artifact is None:
            continue
        path = _contained_file(signed_root, artifact["name"])
        if field != "checksums" and path.stat().st_size != artifact["size"]:
            raise ValueError(f"deployment record {field} size mismatch")
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"deployment record {field} digest mismatch")
        paths[field] = path
    _verify_checksum_manifest(validated, paths)
    return paths


def publish_deployment_record(record: object, signed_root: Path) -> Path:
    paths = verify_record_artifacts(record, signed_root)
    del paths
    validated = _mapping(record, "deployment record")
    channel = Channel.parse(_text(validated["channel"], "deployment record.channel"))
    build_id = _text(validated["build_id"], "deployment record.build_id")
    normalized = validate_deployment_record(
        validated, expected_channel=channel, expected_build_id=build_id
    )
    path = signed_root / "deployments" / channel.device / channel.type / f"{build_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = (json.dumps(normalized, sort_keys=True) + "\n").encode()
    if path.exists():
        if path.is_symlink() or path.read_bytes() != encoded:
            raise FileExistsError(f"deployment record already exists with different data: {path}")
        return path
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    return path


def _artifact_record(root: Path, name: str) -> dict[str, object]:
    path = _contained_file(root, name)
    return {"name": name, "size": path.stat().st_size, "sha256": _sha256(path)}


def _checksum_record(root: Path, name: str) -> dict[str, object]:
    path = _contained_file(root, name)
    return {"name": name, "sha256": _sha256(path)}


def _validate_artifact(
    value: object,
    label: str,
    expected_name: str | None,
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    artifact = _mapping(value, label)
    _require_keys(artifact, expected_keys, label)
    name = _text(artifact["name"], f"{label}.name")
    if Path(name).name != name:
        raise ValueError(f"{label}.name must be a basename")
    if expected_name is not None and name != expected_name:
        raise ValueError(f"{label}.name does not match channel and build ID")
    if "size" in artifact:
        size = artifact["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError(f"{label}.size must be a positive integer")
    validate_sha256(_text(artifact["sha256"], f"{label}.sha256"), f"{label}.sha256")
    return artifact


def _incremental_source_build(channel: Channel, name: str, target: str) -> str:
    prefix = re.escape(channel.full_ota_name(target).removesuffix(f"{target}-signed-ota.zip"))
    pattern = re.compile(
        rf"{prefix}(?P<source>[0-9]{{8}}-[0-9]{{6}})-to-{re.escape(target)}-signed-incremental-ota\.zip"
    )
    match = pattern.fullmatch(name)
    if match is None:
        raise ValueError("incremental artifact name does not match channel and target")
    return match.group("source")


def _contained_file(root: Path, name: object) -> Path:
    text = _text(name, "artifact name")
    if Path(text).name != text:
        raise ValueError("artifact name must be a basename")
    path = root / text
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"artifact is missing or unsupported: {text}")
    if path.parent.resolve() != root.resolve():
        raise ValueError(f"artifact escapes signed root: {text}")
    return path


def _verify_checksum_manifest(record: dict[str, object], paths: dict[str, Path]) -> None:
    lines = paths["checksums"].read_text().splitlines()
    listed: dict[str, str] = {}
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or parts[1] in listed:
            raise ValueError("checksum manifest has invalid entries")
        listed[parts[1]] = parts[0]
    expected = {
        artifact["name"]: artifact["sha256"]
        for field in ("full_ota", "target_files", "incremental")
        if (artifact := record[field]) is not None
    }
    if listed != expected:
        raise ValueError("checksum manifest does not exactly match deployment artifacts")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("deployment record timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: object, label: str) -> datetime:
    text = _text(value, label)
    if not text.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z format")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing")
    value = json.loads(path.read_text())
    return _mapping(value, label)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unexpected keys")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _project_sha() -> str | None:
    project = Path("/opt/yrrp/project")
    if not project.is_dir():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        capture_output=True,
        check=False,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage immutable OTA deployment records")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--channel", required=True)
    prepare.add_argument("--build-id", required=True)
    prepare.add_argument("--signed-root", required=True, type=Path)
    prepare.add_argument("--ota-name", required=True)
    prepare.add_argument("--target-files-name", required=True)
    prepare.add_argument("--incremental-name")
    prepare.add_argument("--output", required=True, type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--record", required=True, type=Path)
    publish.add_argument("--signed-root", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        if arguments.command == "prepare":
            record = build_deployment_record(
                channel=Channel.parse(arguments.channel),
                build_id=arguments.build_id,
                signed_root=arguments.signed_root,
                ota_name=arguments.ota_name,
                target_files_name=arguments.target_files_name,
                incremental_name=arguments.incremental_name,
                created_at=datetime.now(timezone.utc),
                deployer_project_sha=_project_sha(),
            )
            descriptor = os.open(
                arguments.output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(record, output, sort_keys=True)
                output.write("\n")
            return 0
        record = _read_json_object(arguments.record, "deployment record input")
        publish_deployment_record(record, arguments.signed_root)
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"deployment record error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

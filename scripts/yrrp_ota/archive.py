"""ZIP and build-property helpers for signed OTA and target-files archives."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

SYSTEM_PROP_MEMBERS = ("SYSTEM/etc/build.prop", "SYSTEM/build.prop")
METADATA_MEMBER = "META-INF/com/android/metadata"


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_metadata(archive: zipfile.ZipFile, source: str) -> dict[str, str]:
    return parse_properties(archive.read(METADATA_MEMBER), source)


def read_system_properties(archive: zipfile.ZipFile) -> dict[str, str]:
    names = archive.namelist()
    member = next((name for name in SYSTEM_PROP_MEMBERS if name in names), None)
    if member is None:
        raise ValueError("target-files SYSTEM build.prop is missing")
    return parse_properties(archive.read(member), "target-files SYSTEM build.prop")

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .model import Campaign, CampaignState, FeaturePhase, FeatureRecord, validate_slug

EVIDENCE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SENSITIVE = re.compile(
    rb"(?i)(authorization:\s*bearer|password\s*[=:]|private key|"
    rb"access[_ -]?token|client[_ -]?secret|connection[_ -]?string)"
)


class CampaignStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path escapes campaign root: {path}")
        return resolved

    def json_path(self, campaign_id: str) -> Path:
        validate_slug(campaign_id)
        return self._contained(self.root / f"{campaign_id}.json")

    def markdown_path(self, campaign_id: str) -> Path:
        validate_slug(campaign_id)
        return self._contained(self.root / f"{campaign_id}.md")

    @contextmanager
    def lock(self) -> Iterator[None]:
        self._ensure_root()
        lock_path = self._contained(self.root / ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _atomic_write(self, path: Path, content: bytes) -> None:
        self._ensure_root()
        path = self._contained(path)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _save_unlocked(self, campaign: Campaign) -> None:
        payload = json.dumps(campaign.to_dict(), indent=2, sort_keys=True) + "\n"
        self._atomic_write(self.json_path(campaign.campaign_id), payload.encode())
        self._atomic_write(
            self.markdown_path(campaign.campaign_id),
            self.render(campaign).encode(),
        )

    def save(self, campaign: Campaign) -> None:
        with self.lock():
            self._save_unlocked(campaign)

    def _load_unlocked(self, campaign_id: str) -> Campaign:
        value = json.loads(self.json_path(campaign_id).read_text(encoding="utf-8"))
        value["features"] = {
            key: self._feature_from_dict(item)
            for key, item in value.get("features", {}).items()
        }
        value["state"] = CampaignState(value["state"])
        return Campaign(**value)

    @staticmethod
    def _feature_from_dict(value: dict[str, Any]) -> FeatureRecord:
        return FeatureRecord(
            feature_id=value["feature_id"],
            sender=value["sender"],
            phase=FeaturePhase(value["phase"]),
            spec=value["spec"],
            plan=value["plan"],
            repositories=list(value["repositories"]),
            cheap_checks=list(value["cheap_checks"]),
            device_cases=list(value["device_cases"]),
            readiness=value.get("readiness"),
            device_result=value.get("device_result"),
        )

    def load(self, campaign_id: str) -> Campaign:
        with self.lock():
            return self._load_unlocked(campaign_id)

    def mutate(
        self,
        campaign_id: str,
        operation: Callable[[Campaign], None],
    ) -> Campaign:
        with self.lock():
            campaign = self._load_unlocked(campaign_id)
            operation(campaign)
            self._save_unlocked(campaign)
            return campaign

    def set_active(self, campaign_id: str) -> None:
        validate_slug(campaign_id)
        with self.lock():
            self._atomic_write(self.root / "active", f"{campaign_id}\n".encode())

    def get_active(self) -> str:
        with self.lock():
            return validate_slug(
                (self.root / "active").read_text(encoding="utf-8").strip()
            )

    def write_evidence(self, campaign_id: str, name: str, content: bytes) -> str:
        if not EVIDENCE_NAME.fullmatch(name):
            raise ValueError(f"invalid evidence name: {name!r}")
        if SENSITIVE.search(content):
            raise ValueError("evidence contains sensitive material; attach a redacted extract")
        with self.lock():
            directory = self._contained(
                self.root / "evidence" / validate_slug(campaign_id)
            )
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            path = self._contained(directory / name)
            self._atomic_write(path, content)
        return str(path.relative_to(self.root))

    @staticmethod
    def _json_block(value: Any) -> list[str]:
        return ["```json", json.dumps(value, indent=2, sort_keys=True), "```", ""]

    def render(self, campaign: Campaign) -> str:
        lines = self._render_header(campaign)
        lines.extend(self._render_features(campaign))
        lines.extend(self._render_records("Build attempts", campaign.build_attempts))
        lines.extend(
            self._render_records(
                "Device matrix",
                [campaign.device_cases[key] for key in sorted(campaign.device_cases)],
            )
        )
        lines.extend(self._render_records("Failures", campaign.failures))
        return "\n".join(lines)

    def _render_header(self, campaign: Campaign) -> list[str]:
        lines = [
            f"# Build campaign: {campaign.campaign_id}",
            "",
            f"- State: `{campaign.state}`",
            f"- Schema: `{campaign.schema_version}`",
            f"- Active device lease: `{campaign.active_device_lease}`",
            "",
            "## Frozen source",
            "",
        ]
        lines.extend(self._json_block(campaign.source_snapshot))
        return lines

    def _render_features(self, campaign: Campaign) -> list[str]:
        lines = ["## Features", ""]
        for feature_id in sorted(campaign.features):
            feature = campaign.features[feature_id]
            lines.extend(self._render_feature(feature))
        return lines

    def _render_feature(self, feature: FeatureRecord) -> list[str]:
        repositories = ", ".join(f"`{item}`" for item in feature.repositories)
        cheap_checks = ", ".join(f"`{item}`" for item in feature.cheap_checks)
        lines = [
            f"### {feature.feature_id}",
            "",
            f"- Session: `{feature.sender}`",
            f"- Phase: `{feature.phase}`",
            f"- Spec: `{feature.spec}`",
            f"- Plan: `{feature.plan}`",
            f"- Repositories: {repositories}",
            f"- Cheap checks: {cheap_checks}",
            "",
            "Readiness:",
            "",
        ]
        lines.extend(self._json_block(feature.readiness))
        lines.extend(["Device result:", ""])
        lines.extend(self._json_block(feature.device_result))
        return lines

    def _render_records(self, title: str, records: list[dict[str, Any]]) -> list[str]:
        lines = [f"## {title}", ""]
        for record in records:
            lines.extend(self._json_block(record))
        return lines

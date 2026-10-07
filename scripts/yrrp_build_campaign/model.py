from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PERSISTED_FIELDS = {
    "adb_command",
    "api_key",
    "command",
    "connection_string",
    "credentials",
    "env",
    "environment",
    "password",
    "private_key",
    "raw_command",
    "secret",
    "token",
}
REGISTRATION_FIELDS = {
    "feature_id",
    "phase",
    "spec",
    "plan",
    "repositories",
    "cheap_checks",
    "device_cases",
}
READINESS_FIELDS = {
    "revisions",
    "clean_repositories",
    "check_evidence",
    "observability",
    "restoration_steps",
}
DEVICE_CASE_FIELDS = {
    "case_id",
    "setup_checks",
    "action_id",
    "expected",
    "cleanup_checks",
}
RAW_COMMAND_VALUE = re.compile(
    r"^\s*(?:adb|ssh|mka?|atest|brunch|bash|sh|python\d*)\b",
    re.IGNORECASE,
)
SNAPSHOT_FIELDS = {
    "manifest_sha256",
    "manifest_evidence",
    "repositories",
    "branches",
    "clean_repositories",
    "project_sha",
    "build_mode",
    "matrix_sha256",
    "concern_inventory_sha256",
}


class CampaignState(StrEnum):
    COLLECTING = "COLLECTING"
    IMPLEMENTING = "IMPLEMENTING"
    PREFLIGHT = "PREFLIGHT"
    READY_TO_FREEZE = "READY_TO_FREEZE"
    FROZEN = "FROZEN"
    BUILDING = "BUILDING"
    BUILT = "BUILT"
    TESTING = "TESTING"
    FIX_BATCH_READY = "FIX_BATCH_READY"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


class FeaturePhase(StrEnum):
    UNREGISTERED = "UNREGISTERED"
    BRAINSTORMING = "BRAINSTORMING"
    SPEC_APPROVED = "SPEC_APPROVED"
    PLANNING = "PLANNING"
    IMPLEMENTING = "IMPLEMENTING"
    PREFLIGHT = "PREFLIGHT"
    READY_FOR_BUILD = "READY_FOR_BUILD"
    AWAITING_DEVICE_LEASE = "AWAITING_DEVICE_LEASE"
    DEVICE_TESTING = "DEVICE_TESTING"
    FIXING = "FIXING"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


TRANSITIONS = {
    CampaignState.COLLECTING: {CampaignState.IMPLEMENTING, CampaignState.BLOCKED},
    CampaignState.IMPLEMENTING: {CampaignState.PREFLIGHT, CampaignState.BLOCKED},
    CampaignState.PREFLIGHT: {
        CampaignState.READY_TO_FREEZE,
        CampaignState.IMPLEMENTING,
        CampaignState.BLOCKED,
    },
    CampaignState.READY_TO_FREEZE: {
        CampaignState.FROZEN,
        CampaignState.COLLECTING,
        CampaignState.BLOCKED,
    },
    CampaignState.FROZEN: {
        CampaignState.BUILDING,
        CampaignState.READY_TO_FREEZE,
        CampaignState.BLOCKED,
    },
    CampaignState.BUILDING: {
        CampaignState.BUILT,
        CampaignState.FIX_BATCH_READY,
        CampaignState.BLOCKED,
    },
    CampaignState.BUILT: {CampaignState.TESTING, CampaignState.BLOCKED},
    CampaignState.TESTING: {
        CampaignState.ACCEPTED,
        CampaignState.FIX_BATCH_READY,
        CampaignState.BLOCKED,
    },
    CampaignState.FIX_BATCH_READY: {
        CampaignState.IMPLEMENTING,
        CampaignState.BLOCKED,
    },
    CampaignState.ACCEPTED: set(),
    CampaignState.BLOCKED: {
        CampaignState.COLLECTING,
        CampaignState.IMPLEMENTING,
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_slug(value: str) -> str:
    if not SLUG.fullmatch(value):
        raise ValueError(f"invalid slug: {value!r}")
    return value


def validate_repository(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"invalid repository path: {value!r}")
    return value


def validate_safe_payload(value: Any, label: str) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PERSISTED_FIELDS.intersection(value)
        if forbidden:
            raise ValueError(
                f"{label} contains forbidden fields: {', '.join(sorted(forbidden))}"
            )
        for nested in value.values():
            validate_safe_payload(nested, label)
    elif isinstance(value, list):
        for nested in value:
            validate_safe_payload(nested, label)
    elif isinstance(value, str) and RAW_COMMAND_VALUE.match(value):
        raise ValueError(f"{label} contains raw command text")


def digest_json(value: Any) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_check(value: dict[str, Any]) -> dict[str, str]:
    required = {"check_id", "command_sha256", "location"}
    if set(value) != required:
        raise ValueError("cheap check requires check_id, command_sha256, and location")
    check_id = validate_slug(str(value["check_id"]))
    command_sha256 = str(value["command_sha256"])
    if not SHA256.fullmatch(command_sha256):
        raise ValueError("cheap check command_sha256 must be lowercase SHA-256")
    location = str(value["location"])
    if location not in {"local", "AndroidBuilder", "device"}:
        raise ValueError(f"unsupported check location: {location}")
    return {
        "check_id": check_id,
        "command_sha256": command_sha256,
        "location": location,
    }


def _validate_command_refs(
    values: Any,
    label: str,
    *,
    require_nonempty: bool,
) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    if require_nonempty and not values:
        raise ValueError(f"{label} must not be empty")
    return [_validate_check(dict(item)) for item in values]


def _validate_device_cases(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not values:
        raise ValueError("at least one device case is required")
    seen: set[str] = set()
    result = []
    for value in values:
        if set(value) != DEVICE_CASE_FIELDS:
            raise ValueError("device case fields do not match required schema")
        case_id = validate_slug(str(value["case_id"]))
        action_id = validate_slug(str(value["action_id"]))
        expected = str(value["expected"]).strip()
        if not expected:
            raise ValueError("device case expected result is required")
        if case_id in seen:
            raise ValueError(f"duplicate assigned device case: {case_id}")
        seen.add(case_id)
        result.append(
            {
                "case_id": case_id,
                "setup_checks": _validate_command_refs(
                    value["setup_checks"],
                    "device setup checks",
                    require_nonempty=False,
                ),
                "action_id": action_id,
                "expected": expected,
                "cleanup_checks": _validate_command_refs(
                    value["cleanup_checks"],
                    "device cleanup checks",
                    require_nonempty=False,
                ),
            }
        )
    validate_safe_payload(result, "device cases")
    return result


@dataclass
class FeatureRecord:
    feature_id: str
    sender: str
    phase: FeaturePhase
    spec: str
    plan: str
    repositories: list[str]
    cheap_checks: list[dict[str, str]]
    device_cases: list[dict[str, Any]]
    readiness: dict[str, Any] | None = None
    device_result: dict[str, Any] | None = None

    @classmethod
    def from_registration(
        cls,
        value: dict[str, Any],
        sender: str,
    ) -> FeatureRecord:
        if set(value) != REGISTRATION_FIELDS:
            raise ValueError("registration fields do not match required schema")
        repositories = [
            validate_repository(str(item)) for item in value["repositories"]
        ]
        cheap_checks = [_validate_check(dict(item)) for item in value["cheap_checks"]]
        if not repositories:
            raise ValueError("at least one repository is required")
        if not cheap_checks:
            raise ValueError("at least one cheap check is required")
        device_cases = _validate_device_cases(
            [dict(item) for item in value["device_cases"]]
        )
        return cls(
            feature_id=validate_slug(str(value["feature_id"])),
            sender=sender,
            phase=FeaturePhase(str(value["phase"])),
            spec=str(value["spec"]),
            plan=str(value["plan"]),
            repositories=repositories,
            cheap_checks=cheap_checks,
            device_cases=device_cases,
        )

    def mark_ready(self, readiness: dict[str, Any]) -> None:
        if self.phase == FeaturePhase.UNREGISTERED or not self.sender:
            raise ValueError(f"feature {self.feature_id} is not registered")
        if set(readiness) != READINESS_FIELDS:
            raise ValueError("readiness fields do not match required schema")
        validate_safe_payload(readiness, "readiness")
        revisions = readiness["revisions"]
        if set(revisions) != set(self.repositories):
            raise ValueError("readiness revisions do not match registered repositories")
        if not all(SHA1.fullmatch(str(value)) for value in revisions.values()):
            raise ValueError("every revision must be a 40-character lowercase SHA")
        if set(readiness.get("clean_repositories", [])) != set(self.repositories):
            raise ValueError("every repository must be clean")
        if not readiness.get("check_evidence"):
            raise ValueError("check evidence is required")
        if readiness.get("observability") not in {"ready", "not-applicable"}:
            raise ValueError("observability must be ready or not-applicable")
        restoration_steps = _validate_command_refs(
            readiness["restoration_steps"],
            "restoration steps",
            require_nonempty=True,
        )
        self.readiness = {**readiness, "restoration_steps": restoration_steps}
        self.device_result = None
        self.phase = FeaturePhase.READY_FOR_BUILD


@dataclass
class Campaign:
    campaign_id: str
    state: CampaignState = CampaignState.COLLECTING
    schema_version: int = 2
    features: dict[str, FeatureRecord] = field(default_factory=dict)
    source_snapshot: dict[str, Any] | None = None
    approvals: list[dict[str, Any]] = field(default_factory=list)
    preflight_authorizations: list[str] = field(default_factory=list)
    recovery_authorizations: list[str] = field(default_factory=list)
    launcher_authorizations: list[dict[str, Any]] = field(default_factory=list)
    build_attempts: list[dict[str, Any]] = field(default_factory=list)
    installation: dict[str, Any] | None = None
    device_cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    active_device_lease: str | None = None

    @classmethod
    def new(cls, campaign_id: str) -> Campaign:
        return cls(campaign_id=validate_slug(campaign_id))

    def transition(self, target: CampaignState) -> None:
        if target not in TRANSITIONS[self.state]:
            raise ValueError(f"invalid transition: {self.state} -> {target}")
        self.state = target

    def record_event(
        self,
        kind: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {
                "kind": kind,
                "actor": actor,
                "at": utc_now(),
                "details": details or {},
            }
        )

    def freeze(
        self,
        snapshot: dict[str, Any],
        approval: str,
        actor: str,
    ) -> None:
        if self.state != CampaignState.READY_TO_FREEZE:
            raise ValueError("campaign is not ready to freeze")
        self._validate_feature_readiness()
        self._validate_snapshot(snapshot)
        expected = "Freeze fixes and rebuild" if self.build_attempts else "Freeze and build"
        if approval != expected:
            raise ValueError(f"explicit {expected} approval is required")
        if self.preflight_authorizations:
            raise ValueError("unused preflight authorizations must be cleared")
        snapshot_copy = json.loads(json.dumps(snapshot, sort_keys=True))
        self.source_snapshot = snapshot_copy
        approval_record = {
            "kind": "follow-up-freeze" if self.build_attempts else "freeze",
            "choice": approval,
            "actor": actor,
            "at": utc_now(),
            "snapshot_sha256": digest_json(snapshot_copy),
        }
        self.approvals.append(approval_record)
        self.record_event("frozen", actor, approval_record)
        self.transition(CampaignState.FROZEN)

    def _validate_feature_readiness(self) -> None:
        allowed = {FeaturePhase.READY_FOR_BUILD, FeaturePhase.ACCEPTED}
        if not self.features or any(
            feature.phase not in allowed or feature.readiness is None
            for feature in self.features.values()
        ):
            raise ValueError("every feature must be ready or previously accepted")
        if self.build_attempts and not any(
            feature.phase == FeaturePhase.READY_FOR_BUILD
            for feature in self.features.values()
        ):
            raise ValueError("follow-up freeze requires at least one changed feature")

    def _validate_snapshot(self, snapshot: dict[str, Any]) -> None:
        missing = sorted(SNAPSHOT_FIELDS - snapshot.keys())
        if missing:
            raise ValueError(f"snapshot missing: {', '.join(missing)}")
        unknown = sorted(set(snapshot) - SNAPSHOT_FIELDS)
        if unknown:
            raise ValueError(f"snapshot has unknown fields: {', '.join(unknown)}")
        for field_name in (
            "manifest_sha256",
            "matrix_sha256",
            "concern_inventory_sha256",
        ):
            if not SHA256.fullmatch(str(snapshot[field_name])):
                raise ValueError(f"snapshot {field_name} must be lowercase SHA-256")
        if not SHA1.fullmatch(str(snapshot["project_sha"])):
            raise ValueError("snapshot project_sha must be lowercase SHA")
        if not str(snapshot["manifest_evidence"]).startswith("evidence/"):
            raise ValueError("snapshot manifest_evidence must reference private evidence")
        if snapshot["build_mode"] != "signed-ota":
            raise ValueError("snapshot build_mode must be signed-ota")
        expected = self.expected_revisions()
        if snapshot["repositories"] != expected:
            raise ValueError("snapshot revisions do not match feature readiness")
        if set(snapshot["branches"]) != set(expected):
            raise ValueError("snapshot branches do not match repositories")
        if set(snapshot["clean_repositories"]) != set(expected):
            raise ValueError("snapshot clean repositories do not match revisions")
        if snapshot["matrix_sha256"] != digest_json(self.device_matrix()):
            raise ValueError("snapshot matrix hash does not match device cases")
        if snapshot["concern_inventory_sha256"] != digest_json(
            self.concern_inventory()
        ):
            raise ValueError("snapshot concern hash does not match feature inventory")

    def expected_revisions(self) -> dict[str, str]:
        revisions: dict[str, str] = {}
        for feature in self.features.values():
            if feature.readiness is None:
                continue
            for repository, revision in feature.readiness["revisions"].items():
                existing = revisions.get(repository)
                if existing is not None and existing != revision:
                    raise ValueError(f"conflicting revision for {repository}")
                revisions[repository] = revision
        return revisions

    def concern_inventory(self) -> dict[str, Any]:
        return {
            feature_id: {
                "repositories": list(feature.repositories),
                "revisions": dict(feature.readiness["revisions"]),
            }
            for feature_id, feature in sorted(self.features.items())
            if feature.readiness is not None
        }

    def device_matrix(self) -> dict[str, list[dict[str, Any]]]:
        return {
            feature_id: list(feature.device_cases)
            for feature_id, feature in sorted(self.features.items())
        }

    def prepare_snapshot(self, live_source: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            **live_source,
            "build_mode": "signed-ota",
            "matrix_sha256": digest_json(self.device_matrix()),
            "concern_inventory_sha256": digest_json(self.concern_inventory()),
        }
        self._validate_feature_readiness()
        self._validate_snapshot(snapshot)
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

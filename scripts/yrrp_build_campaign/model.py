from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CampaignState(StrEnum):
    COLLECTING = "COLLECTING"
    IMPLEMENTING = "IMPLEMENTING"
    PREFLIGHT = "PREFLIGHT"
    READY_TO_FREEZE = "READY_TO_FREEZE"
    FROZEN = "FROZEN"
    BUILDING = "BUILDING"
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
        CampaignState.TESTING,
        CampaignState.FIX_BATCH_READY,
        CampaignState.BLOCKED,
    },
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


def validate_slug(value: str) -> str:
    if not SLUG.fullmatch(value):
        raise ValueError(f"invalid slug: {value!r}")
    return value


@dataclass
class FeatureRecord:
    feature_id: str
    sender: str
    phase: FeaturePhase
    spec: str
    plan: str
    repositories: list[str]
    cheap_checks: list[str]
    device_cases: list[dict[str, Any]]
    readiness: dict[str, Any] | None = None
    device_result: dict[str, Any] | None = None

    @classmethod
    def from_registration(
        cls,
        value: dict[str, Any],
        sender: str,
    ) -> FeatureRecord:
        return cls(
            feature_id=validate_slug(str(value["feature_id"])),
            sender=sender,
            phase=FeaturePhase(str(value["phase"])),
            spec=str(value["spec"]),
            plan=str(value["plan"]),
            repositories=[str(item) for item in value["repositories"]],
            cheap_checks=[str(item) for item in value["cheap_checks"]],
            device_cases=[dict(item) for item in value["device_cases"]],
        )

    def mark_ready(self, readiness: dict[str, Any]) -> None:
        if self.phase == FeaturePhase.UNREGISTERED or not self.sender:
            raise ValueError(f"feature {self.feature_id} is not registered")
        revisions = readiness.get("revisions", {})
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
        self.readiness = dict(readiness)
        self.phase = FeaturePhase.READY_FOR_BUILD


@dataclass
class Campaign:
    campaign_id: str
    state: CampaignState = CampaignState.COLLECTING
    schema_version: int = 1
    features: dict[str, FeatureRecord] = field(default_factory=dict)
    source_snapshot: dict[str, Any] | None = None
    approvals: list[dict[str, str]] = field(default_factory=list)
    preflight_authorizations: list[str] = field(default_factory=list)
    build_attempts: list[dict[str, Any]] = field(default_factory=list)
    device_cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    active_device_lease: str | None = None

    @classmethod
    def new(cls, campaign_id: str) -> Campaign:
        return cls(campaign_id=validate_slug(campaign_id))

    def transition(self, target: CampaignState) -> None:
        if target not in TRANSITIONS[self.state]:
            raise ValueError(f"invalid transition: {self.state} -> {target}")
        self.state = target

    def freeze(self, snapshot: dict[str, Any], approval: str) -> None:
        if self.state != CampaignState.READY_TO_FREEZE:
            raise ValueError("campaign is not ready to freeze")
        if not self.features or any(
            feature.phase != FeaturePhase.READY_FOR_BUILD
            for feature in self.features.values()
        ):
            raise ValueError("every registered feature must be ready")
        if approval != "Freeze and build":
            raise ValueError("explicit Freeze and build approval is required")
        if not SHA256.fullmatch(str(snapshot.get("manifest_sha256", ""))):
            raise ValueError("snapshot requires manifest_sha256")
        self.source_snapshot = dict(snapshot)
        self.approvals.append({"kind": "freeze", "choice": approval})
        self.transition(CampaignState.FROZEN)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

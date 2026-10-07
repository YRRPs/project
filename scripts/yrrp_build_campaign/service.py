from __future__ import annotations

import hashlib
from typing import Any

from .model import Campaign, CampaignState, FeaturePhase, FeatureRecord
from .store import CampaignStore


def _require_fields(
    payload: dict[str, Any],
    required: set[str],
    label: str,
) -> None:
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"{label} missing: {', '.join(missing)}")


class CampaignService:
    def __init__(self, store: CampaignStore) -> None:
        self.store = store

    def create(self, campaign_id: str) -> Campaign:
        campaign = Campaign.new(campaign_id)
        self.store.save(campaign)
        self.store.set_active(campaign_id)
        return campaign

    def register_feature(
        self,
        campaign_id: str,
        payload: dict[str, Any],
        sender: str,
    ) -> Campaign:
        feature = FeatureRecord.from_registration(payload, sender=sender)

        def apply(campaign: Campaign) -> None:
            existing = campaign.features.get(feature.feature_id)
            if existing is not None and existing.sender != sender:
                raise ValueError("feature is already owned by another session")
            campaign.features[feature.feature_id] = feature

        return self.store.mutate(campaign_id, apply)

    def mark_feature_ready(
        self,
        campaign_id: str,
        feature_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        def apply(campaign: Campaign) -> None:
            campaign.features[feature_id].mark_ready(payload)

        return self.store.mutate(campaign_id, apply)

    def set_feature_phase(
        self,
        campaign_id: str,
        feature_id: str,
        phase: FeaturePhase,
    ) -> Campaign:
        def apply(campaign: Campaign) -> None:
            campaign.features[feature_id].phase = phase

        return self.store.mutate(campaign_id, apply)

    def transition(self, campaign_id: str, target: CampaignState) -> Campaign:
        return self.store.mutate(
            campaign_id,
            lambda campaign: campaign.transition(target),
        )

    def freeze(
        self,
        campaign_id: str,
        snapshot: dict[str, Any],
        approval: str,
    ) -> Campaign:
        return self.store.mutate(
            campaign_id,
            lambda campaign: campaign.freeze(snapshot, approval),
        )

    def authorize_preflight(self, campaign_id: str, command: str) -> str:
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.PREFLIGHT:
                raise ValueError("campaign is not in PREFLIGHT")
            campaign.preflight_authorizations.append(digest)

        self.store.mutate(campaign_id, apply)
        return digest

    def consume_preflight(self, campaign_id: str, command: str) -> None:
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            try:
                campaign.preflight_authorizations.remove(digest)
            except ValueError as error:
                raise ValueError("preflight command is not authorized") from error

        self.store.mutate(campaign_id, apply)

    def claim_build(self, campaign_id: str, command: str) -> Campaign:
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.FROZEN:
                raise ValueError(f"campaign state is {campaign.state}, not FROZEN")
            campaign.build_attempts.append(
                {
                    "number": len(campaign.build_attempts) + 1,
                    "command_sha256": digest,
                    "status": "claimed",
                }
            )
            campaign.transition(CampaignState.BUILDING)

        return self.store.mutate(campaign_id, apply)

    def record_build_result(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        status = self._build_status(payload)

        def apply(campaign: Campaign) -> None:
            self._apply_build_result(campaign, status, payload)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _build_status(payload: dict[str, Any]) -> str:
        _require_fields(payload, {"status", "evidence"}, "build result")
        status = str(payload["status"])
        if status not in {"complete", "failed", "blocked"}:
            raise ValueError(f"unknown build status: {status}")
        return status

    @staticmethod
    def _apply_build_result(
        campaign: Campaign,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if campaign.state != CampaignState.BUILDING:
            raise ValueError("campaign is not in BUILDING")
        if not campaign.build_attempts:
            raise ValueError("no claimed build attempt")
        campaign.build_attempts[-1].update(
            {"status": status, "evidence": list(payload["evidence"])}
        )
        campaign.transition(
            {
                "complete": CampaignState.TESTING,
                "failed": CampaignState.FIX_BATCH_READY,
                "blocked": CampaignState.BLOCKED,
            }[status]
        )

    def grant_device_lease(self, campaign_id: str, feature_id: str) -> Campaign:
        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.TESTING:
                raise ValueError("campaign is not in TESTING")
            if campaign.active_device_lease is not None:
                raise ValueError(f"device leased to {campaign.active_device_lease}")
            campaign.features[feature_id].phase = FeaturePhase.DEVICE_TESTING
            campaign.active_device_lease = feature_id

        return self.store.mutate(campaign_id, apply)

    def record_device_result(
        self,
        campaign_id: str,
        feature_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        self._validate_device_result(payload)

        def apply(campaign: Campaign) -> None:
            self._apply_device_result(campaign, feature_id, payload)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _validate_device_result(payload: dict[str, Any]) -> None:
        required = {
            "build_id",
            "passed",
            "failed",
            "blocked",
            "not_run",
            "restored_state",
            "evidence",
            "next_phase",
        }
        _require_fields(payload, required, "device result")

    @staticmethod
    def _apply_device_result(
        campaign: Campaign,
        feature_id: str,
        payload: dict[str, Any],
    ) -> None:
        if campaign.active_device_lease != feature_id:
            raise ValueError("feature does not hold device lease")
        feature = campaign.features[feature_id]
        feature.device_result = dict(payload)
        feature.phase = FeaturePhase(payload["next_phase"])
        campaign.active_device_lease = None
        if payload["failed"]:
            campaign.failures.extend(
                dict(item) for item in payload.get("failures", [])
            )

    def record_case(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        case_id = str(payload["case_id"])
        result = str(payload["result"])
        if result not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise ValueError(f"unknown case result: {result}")

        def apply(campaign: Campaign) -> None:
            if case_id in campaign.device_cases:
                raise ValueError(f"duplicate device case: {case_id}")
            campaign.device_cases[case_id] = dict(payload)

        return self.store.mutate(campaign_id, apply)

    def record_failure(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        _require_fields(payload, {"kind", "feature_id", "reason"}, "failure")

        def apply(campaign: Campaign) -> None:
            campaign.failures.append(dict(payload))

        return self.store.mutate(campaign_id, apply)

    def prepare_follow_up(self, campaign_id: str, approval: str) -> Campaign:
        if approval != "Freeze fixes and rebuild":
            raise ValueError("explicit follow-up approval is required")

        def apply(campaign: Campaign) -> None:
            self._validate_follow_up(campaign)
            campaign.approvals.append(
                {"kind": "follow-up", "choice": approval}
            )
            campaign.transition(CampaignState.IMPLEMENTING)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _validate_follow_up(campaign: Campaign) -> None:
        if campaign.state != CampaignState.FIX_BATCH_READY:
            raise ValueError("campaign is not in FIX_BATCH_READY")
        if campaign.active_device_lease is not None:
            raise ValueError("device lease is still active")
        affected = {
            str(failure["feature_id"])
            for failure in campaign.failures
            if "feature_id" in failure
        }
        if not affected:
            raise ValueError("follow-up requires feature failures")
        if any(
            campaign.features[feature_id].phase != FeaturePhase.READY_FOR_BUILD
            for feature_id in affected
        ):
            raise ValueError("every affected feature owner must be ready")

    def invalidate_freeze(self, campaign_id: str, reason: str) -> Campaign:
        if not reason.strip():
            raise ValueError("freeze invalidation reason is required")

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.FROZEN:
                raise ValueError("only a frozen campaign can be invalidated")
            campaign.source_snapshot = None
            campaign.failures.append(
                {"kind": "freeze-invalidated", "reason": reason.strip()}
            )
            campaign.transition(CampaignState.READY_TO_FREEZE)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def command_digest(command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()

    def active_campaign_id(self) -> str:
        return self.store.get_active()

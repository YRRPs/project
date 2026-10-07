from __future__ import annotations

import hashlib
import time
from typing import Any

from .command_policy import is_supported_preflight_command, is_supported_recovery_command
from .model import (
    Campaign,
    CampaignState,
    FeaturePhase,
    FeatureRecord,
    digest_json,
    validate_safe_payload,
)
from .store import CampaignStore

CAMPAIGN_AGENT = "yrrp-build-campaign"
PUBLIC_TRANSITIONS = {
    CampaignState.COLLECTING,
    CampaignState.IMPLEMENTING,
    CampaignState.PREFLIGHT,
    CampaignState.READY_TO_FREEZE,
    CampaignState.BLOCKED,
}
PUBLIC_FEATURE_PHASES = {
    FeaturePhase.BRAINSTORMING,
    FeaturePhase.SPEC_APPROVED,
    FeaturePhase.PLANNING,
    FeaturePhase.IMPLEMENTING,
    FeaturePhase.PREFLIGHT,
    FeaturePhase.FIXING,
    FeaturePhase.BLOCKED,
}
IMMUTABLE_STATES = {
    CampaignState.FROZEN,
    CampaignState.BUILDING,
    CampaignState.BUILT,
    CampaignState.TESTING,
    CampaignState.ACCEPTED,
}
BUILD_RESULT_FIELDS = {
    "build_id",
    "status",
    "status_file",
    "log_path",
    "project_sha",
    "started_at",
    "completed_at",
    "final_status",
    "signed_artifact_evidence",
    "public_ota_evidence",
}
INSTALLATION_FIELDS = {
    "build_id",
    "installed_at",
    "source_snapshot_sha256",
    "baseline_evidence",
}


def _require_fields(
    payload: dict[str, Any],
    required: set[str],
    label: str,
    optional: set[str] | None = None,
) -> None:
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"{label} missing: {', '.join(missing)}")
    unknown = sorted(set(payload) - required - (optional or set()))
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")


class CampaignService:
    def __init__(self, store: CampaignStore) -> None:
        self.store = store

    def create(self, campaign_id: str) -> Campaign:
        campaign = Campaign.new(campaign_id)
        campaign.record_event("created", "campaign-cli")
        self.store.create(campaign)
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
            self._require_feature_mutation(campaign)
            existing = campaign.features.get(feature.feature_id)
            if existing is not None and existing.sender != sender:
                raise ValueError("feature is already owned by another session")
            campaign.features[feature.feature_id] = feature
            campaign.record_event(
                "feature-registered",
                sender,
                {"feature_id": feature.feature_id},
            )

        return self.store.mutate(campaign_id, apply)

    def mark_feature_ready(
        self,
        campaign_id: str,
        feature_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        def apply(campaign: Campaign) -> None:
            self._require_feature_mutation(campaign)
            feature = campaign.features[feature_id]
            feature.mark_ready(payload)
            campaign.record_event(
                "feature-ready",
                feature.sender,
                {
                    "feature_id": feature_id,
                    "revisions": dict(payload["revisions"]),
                },
            )

        return self.store.mutate(campaign_id, apply)

    def set_feature_phase(
        self,
        campaign_id: str,
        feature_id: str,
        phase: FeaturePhase,
    ) -> Campaign:
        if phase not in PUBLIC_FEATURE_PHASES:
            raise ValueError(f"guarded feature phase requires dedicated operation: {phase}")

        def apply(campaign: Campaign) -> None:
            self._require_feature_mutation(campaign)
            feature = campaign.features[feature_id]
            feature.phase = phase
            campaign.record_event(
                "feature-phase",
                feature.sender,
                {"feature_id": feature_id, "phase": phase},
            )

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _require_feature_mutation(campaign: Campaign) -> None:
        if campaign.state in IMMUTABLE_STATES:
            raise ValueError(f"campaign state {campaign.state} is immutable")

    def transition(
        self,
        campaign_id: str,
        target: CampaignState,
        actor: str = "campaign-cli",
    ) -> Campaign:
        if target not in PUBLIC_TRANSITIONS:
            raise ValueError(f"guarded state requires dedicated operation: {target}")

        def apply(campaign: Campaign) -> None:
            previous = campaign.state
            campaign.transition(target)
            campaign.record_event(
                "state-transition",
                actor,
                {"from": previous, "to": target},
            )

        return self.store.mutate(campaign_id, apply)

    def prepare_snapshot(
        self,
        campaign_id: str,
        live_source: dict[str, Any],
    ) -> dict[str, Any]:
        campaign = self.store.load(campaign_id)
        if campaign.state != CampaignState.READY_TO_FREEZE:
            raise ValueError("campaign is not ready to prepare a snapshot")
        return campaign.prepare_snapshot(live_source)

    def freeze(
        self,
        campaign_id: str,
        snapshot: dict[str, Any],
        approval: str,
        actor: str,
    ) -> Campaign:
        if "manifest_evidence" not in snapshot or "manifest_sha256" not in snapshot:
            raise ValueError("snapshot requires manifest evidence and digest")
        self.store.verify_evidence(
            campaign_id,
            str(snapshot["manifest_evidence"]),
            str(snapshot["manifest_sha256"]),
        )
        return self.store.mutate_unique_frozen(
            campaign_id,
            lambda campaign: campaign.freeze(snapshot, approval, actor),
        )

    def authorize_preflight(self, campaign_id: str, command: str) -> str:
        if not is_supported_preflight_command(command):
            raise ValueError("unsupported preflight command structure")
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.PREFLIGHT:
                raise ValueError("campaign is not in PREFLIGHT")
            registered = {
                check["command_sha256"]
                for feature in campaign.features.values()
                for check in feature.cheap_checks
            }
            if digest not in registered:
                raise ValueError("preflight command is not registered")
            campaign.preflight_authorizations.append(digest)
            campaign.record_event(
                "preflight-authorized",
                "campaign-cli",
                {"command_sha256": digest},
            )

        self.store.mutate(campaign_id, apply)
        return digest

    def authorize_recovery(self, campaign_id: str, command: str) -> str:
        """Allow one exact builder rerun that only the campaign session may consume."""
        if not is_supported_recovery_command(command):
            raise ValueError("unsupported recovery command structure")
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            if campaign.state == CampaignState.FROZEN:
                raise ValueError("campaign is FROZEN; launch or invalidate the freeze first")
            campaign.recovery_authorizations.append(digest)
            campaign.record_event(
                "recovery-authorized",
                "campaign-cli",
                {"command_sha256": digest},
            )

        self.store.mutate(campaign_id, apply)
        return digest

    def consume_recovery(self, campaign_id: str, command: str, agent_type: str) -> None:
        if agent_type != CAMPAIGN_AGENT:
            raise ValueError(f"only the {CAMPAIGN_AGENT} session may run recovery commands")
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            try:
                campaign.recovery_authorizations.remove(digest)
            except ValueError as error:
                raise ValueError("recovery command is not authorized") from error
            campaign.record_event(
                "recovery-consumed",
                "campaign-hook",
                {"command_sha256": digest, "agent_type": agent_type},
            )

        self.store.mutate(campaign_id, apply)

    def consume_preflight(self, campaign_id: str, command: str) -> None:
        digest = self.command_digest(command)

        def apply(campaign: Campaign) -> None:
            try:
                campaign.preflight_authorizations.remove(digest)
            except ValueError as error:
                raise ValueError("preflight command is not authorized") from error
            campaign.record_event(
                "preflight-consumed",
                "campaign-hook",
                {"command_sha256": digest},
            )

        self.store.mutate(campaign_id, apply)

    def authorize_launcher(
        self,
        campaign_id: str,
        actor: str,
        agent_type: str,
    ) -> Campaign:
        if agent_type != CAMPAIGN_AGENT:
            raise ValueError("only yrrp-build-campaign may launch a build")

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.FROZEN:
                raise ValueError(f"campaign state is {campaign.state}, not FROZEN")
            approved_agent = str(campaign.approvals[-1]["actor"])
            if agent_type != approved_agent:
                raise ValueError("build agent type does not match freeze approval")
            campaign.launcher_authorizations.append(
                {
                    "actor": actor,
                    "agent_type": agent_type,
                    "expires_at": time.time() + 300,
                }
            )
            campaign.record_event("launcher-authorized", actor)

        return self.store.mutate(campaign_id, apply)

    def consume_launcher_authorization(self, campaign_id: str) -> dict[str, Any]:
        consumed: dict[str, Any] = {}

        def apply(campaign: Campaign) -> None:
            now = time.time()
            campaign.launcher_authorizations[:] = [
                item
                for item in campaign.launcher_authorizations
                if float(item["expires_at"]) >= now
            ]
            if not campaign.launcher_authorizations:
                raise ValueError("launcher has no valid orchestrator authorization")
            consumed.update(campaign.launcher_authorizations.pop(0))
            campaign.record_event("launcher-authorization-consumed", consumed["actor"])

        self.store.mutate(campaign_id, apply)
        return consumed

    def claim_build(
        self,
        campaign_id: str,
        current_snapshot: dict[str, Any],
        actor: str,
        agent_type: str = "yrrp-build-campaign",
    ) -> Campaign:
        def apply(campaign: Campaign) -> None:
            self._validate_build_claim(
                campaign,
                current_snapshot,
                agent_type,
            )
            attempt = {
                "number": len(campaign.build_attempts) + 1,
                "actor": actor,
                "agent_type": agent_type,
                "source_snapshot_sha256": digest_json(current_snapshot),
                "status": "claimed",
            }
            campaign.build_attempts.append(attempt)
            campaign.record_event("build-claimed", actor, attempt)
            campaign.transition(CampaignState.BUILDING)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _validate_build_claim(
        campaign: Campaign,
        current_snapshot: dict[str, Any],
        agent_type: str,
    ) -> None:
        if campaign.state != CampaignState.FROZEN:
            raise ValueError(f"campaign state is {campaign.state}, not FROZEN")
        if campaign.source_snapshot != current_snapshot:
            raise ValueError("current source does not match frozen snapshot")
        if not campaign.approvals or campaign.approvals[-1]["actor"] != agent_type:
            raise ValueError("build agent type does not match freeze approval")

    def record_launch_failure(
        self,
        campaign_id: str,
        reason: str,
    ) -> Campaign:
        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.BUILDING:
                raise ValueError("campaign is not in BUILDING")
            campaign.build_attempts[-1]["status"] = "launch-failed"
            campaign.build_attempts[-1]["failure"] = reason
            campaign.record_event("build-launch-failed", "build-launcher", {"reason": reason})
            campaign.transition(CampaignState.FIX_BATCH_READY)

        return self.store.mutate(campaign_id, apply)

    def record_build_result(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        _require_fields(payload, BUILD_RESULT_FIELDS, "build result")
        status = str(payload["status"])
        if status not in {"complete", "failed", "blocked"}:
            raise ValueError(f"unknown build status: {status}")

        def apply(campaign: Campaign) -> None:
            self._apply_build_result(campaign, status, payload)

        return self.store.mutate(campaign_id, apply)

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
        if payload["project_sha"] != campaign.source_snapshot["project_sha"]:
            raise ValueError("build project SHA does not match frozen snapshot")
        campaign.build_attempts[-1].update(dict(payload))
        campaign.record_event(
            "build-result",
            str(campaign.build_attempts[-1]["actor"]),
            {"build_id": payload["build_id"], "status": status},
        )
        campaign.transition(
            {
                "complete": CampaignState.BUILT,
                "failed": CampaignState.FIX_BATCH_READY,
                "blocked": CampaignState.BLOCKED,
            }[status]
        )

    def record_installation(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        _require_fields(payload, INSTALLATION_FIELDS, "installation")

        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.BUILT:
                raise ValueError("campaign is not in BUILT")
            attempt = campaign.build_attempts[-1]
            if payload["build_id"] != attempt["build_id"]:
                raise ValueError("installed build ID does not match built artifact")
            expected = digest_json(campaign.source_snapshot)
            if payload["source_snapshot_sha256"] != expected:
                raise ValueError("installed source snapshot does not match frozen source")
            if not payload["baseline_evidence"]:
                raise ValueError("installation baseline evidence is required")
            campaign.installation = dict(payload)
            campaign.record_event(
                "installed",
                "campaign-cli",
                {"build_id": payload["build_id"]},
            )
            campaign.transition(CampaignState.TESTING)

        return self.store.mutate(campaign_id, apply)

    def grant_device_lease(self, campaign_id: str, feature_id: str) -> Campaign:
        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.TESTING:
                raise ValueError("campaign is not in TESTING")
            if campaign.active_device_lease is not None:
                raise ValueError(f"device leased to {campaign.active_device_lease}")
            feature = campaign.features[feature_id]
            if feature.phase != FeaturePhase.READY_FOR_BUILD:
                raise ValueError(f"feature {feature_id} is not ready for device testing")
            feature.phase = FeaturePhase.DEVICE_TESTING
            campaign.active_device_lease = feature_id
            campaign.record_event("device-lease-granted", feature.sender, {"feature_id": feature_id})

        return self.store.mutate(campaign_id, apply)

    def record_case(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        _require_fields(
            payload,
            {"case_id", "feature_id", "result"},
            "device case",
            {"expected", "observed", "evidence"},
        )
        validate_safe_payload(payload, "device case")
        case_id = str(payload["case_id"])
        result = str(payload["result"])
        if result not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise ValueError(f"unknown case result: {result}")

        def apply(campaign: Campaign) -> None:
            feature_id = str(payload["feature_id"])
            if campaign.active_device_lease != feature_id:
                raise ValueError("feature does not hold device lease")
            assigned = {
                str(item["case_id"])
                for item in campaign.features[feature_id].device_cases
            }
            if case_id not in assigned:
                raise ValueError(f"device case is not assigned: {case_id}")
            if case_id in campaign.device_cases:
                raise ValueError(f"duplicate device case: {case_id}")
            campaign.device_cases[case_id] = dict(payload)
            campaign.record_event("device-case", campaign.features[feature_id].sender, dict(payload))

        return self.store.mutate(campaign_id, apply)

    def record_device_result(
        self,
        campaign_id: str,
        feature_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        self._validate_device_result_shape(payload)

        def apply(campaign: Campaign) -> None:
            self._apply_device_result(campaign, feature_id, payload)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def _validate_device_result_shape(payload: dict[str, Any]) -> None:
        required = {
            "build_id",
            "passed",
            "failed",
            "blocked",
            "not_run",
            "failures",
            "restored_state",
            "evidence",
            "next_phase",
        }
        _require_fields(payload, required, "device result")
        validate_safe_payload(payload, "device result")
        counts = [payload[name] for name in ("passed", "failed", "blocked", "not_run")]
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("device result counts must be non-negative integers")
        if not payload["restored_state"]:
            raise ValueError("restored state is required")
        if not payload["evidence"]:
            raise ValueError("device result evidence is required")

    @staticmethod
    def _apply_device_result(
        campaign: Campaign,
        feature_id: str,
        payload: dict[str, Any],
    ) -> None:
        if campaign.active_device_lease != feature_id:
            raise ValueError("feature does not hold device lease")
        if campaign.installation is None:
            raise ValueError("installed build identity is missing")
        if payload["build_id"] != campaign.installation["build_id"]:
            raise ValueError("device result build ID does not match installation")
        assigned = {
            str(item["case_id"])
            for item in campaign.features[feature_id].device_cases
        }
        feature_cases = {
            case_id: item
            for case_id, item in campaign.device_cases.items()
            if item["feature_id"] == feature_id
        }
        if set(feature_cases) != assigned:
            raise ValueError("recorded cases do not match assigned device cases")
        derived = {
            result: sum(item["result"] == result for item in feature_cases.values())
            for result in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
        }
        supplied = {
            "PASS": payload["passed"],
            "FAIL": payload["failed"],
            "BLOCKED": payload["blocked"],
            "NOT_RUN": payload["not_run"],
        }
        if supplied != derived:
            raise ValueError("device result counts do not match recorded cases")
        CampaignService._validate_result_phase(payload)
        feature = campaign.features[feature_id]
        feature.device_result = dict(payload)
        feature.phase = FeaturePhase(payload["next_phase"])
        campaign.active_device_lease = None
        campaign.failures.extend(dict(item) for item in payload["failures"])
        campaign.record_event(
            "feature-device-result",
            feature.sender,
            {"feature_id": feature_id, "next_phase": feature.phase},
        )

    @staticmethod
    def _validate_result_phase(payload: dict[str, Any]) -> None:
        failed = int(payload["failed"])
        blocked = int(payload["blocked"])
        not_run = int(payload["not_run"])
        failures = payload["failures"]
        if len(failures) != failed:
            raise ValueError("failure details must match failed case count")
        for failure in failures:
            _require_fields(
                failure,
                {"feature_id", "case_id", "observed"},
                "feature failure",
                {"expected", "evidence"},
            )
        if failed and payload["next_phase"] != FeaturePhase.FIXING:
            raise ValueError("failed result must enter FIXING")
        if not failed and (blocked or not_run) and payload["next_phase"] != FeaturePhase.BLOCKED:
            raise ValueError("incomplete result must enter BLOCKED")
        if not failed and not blocked and not not_run and payload["next_phase"] != FeaturePhase.ACCEPTED:
            raise ValueError("passing result must enter ACCEPTED")

    def finalize_testing(self, campaign_id: str) -> Campaign:
        def apply(campaign: Campaign) -> None:
            if campaign.state != CampaignState.TESTING:
                raise ValueError("campaign is not in TESTING")
            if campaign.active_device_lease is not None:
                raise ValueError("device lease is still active")
            phases = {feature.phase for feature in campaign.features.values()}
            if FeaturePhase.DEVICE_TESTING in phases or FeaturePhase.READY_FOR_BUILD in phases:
                raise ValueError("every assigned feature must finish device testing")
            if FeaturePhase.BLOCKED in phases:
                campaign.transition(CampaignState.BLOCKED)
            elif FeaturePhase.FIXING in phases or campaign.failures:
                campaign.transition(CampaignState.FIX_BATCH_READY)
            elif phases == {FeaturePhase.ACCEPTED}:
                campaign.transition(CampaignState.ACCEPTED)
            else:
                raise ValueError("device testing has no valid terminal outcome")
            campaign.record_event("testing-finalized", "campaign-cli", {"state": campaign.state})

        return self.store.mutate(campaign_id, apply)

    def record_failure(
        self,
        campaign_id: str,
        payload: dict[str, Any],
    ) -> Campaign:
        _require_fields(
            payload,
            {"kind", "feature_id", "reason"},
            "failure",
            {"case_id", "expected", "observed", "evidence", "stage"},
        )
        validate_safe_payload(payload, "failure")

        def apply(campaign: Campaign) -> None:
            campaign.failures.append(dict(payload))
            campaign.record_event("failure-recorded", "campaign-cli", dict(payload))

        return self.store.mutate(campaign_id, apply)

    def prepare_follow_up(self, campaign_id: str) -> Campaign:
        def apply(campaign: Campaign) -> None:
            self._validate_follow_up(campaign)
            prior_failures = list(campaign.failures)
            campaign.source_snapshot = None
            campaign.installation = None
            campaign.device_cases.clear()
            campaign.failures.clear()
            campaign.launcher_authorizations.clear()
            campaign.record_event(
                "follow-up-prepared",
                "campaign-cli",
                {"prior_failures": prior_failures},
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
        build_failed = bool(
            campaign.build_attempts
            and campaign.build_attempts[-1].get("status") in {"failed", "launch-failed"}
        )
        if not affected and not build_failed:
            raise ValueError("follow-up requires feature or build failures")
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
            campaign.launcher_authorizations.clear()
            campaign.failures.append(
                {"kind": "freeze-invalidated", "reason": reason.strip()}
            )
            campaign.record_event(
                "freeze-invalidated",
                "campaign-cli",
                {"reason": reason.strip()},
            )
            campaign.transition(CampaignState.READY_TO_FREEZE)

        return self.store.mutate(campaign_id, apply)

    @staticmethod
    def command_digest(command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()

    def active_campaign_id(self) -> str:
        return self.store.get_active()

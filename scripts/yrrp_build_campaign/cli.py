from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .model import Campaign, CampaignState, FeaturePhase
from .service import CampaignService
from .store import CampaignStore

Handler = Callable[[argparse.Namespace, CampaignService, CampaignStore], dict[str, Any]]


def campaign_root() -> Path:
    configured = os.environ.get("YRRP_CAMPAIGN_ROOT")
    if configured:
        return Path(configured)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd()))
    return project / ".claude" / "build-campaigns"


def _campaign_command(commands: Any, name: str) -> argparse.ArgumentParser:
    command = commands.add_parser(name)
    command.add_argument("--campaign-id", required=True)
    return command


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage one YRRP build campaign")
    commands = result.add_subparsers(dest="command", required=True)
    _campaign_command(commands, "create")
    register = _campaign_command(commands, "register-feature")
    register.add_argument("--sender", required=True)
    ready = _campaign_command(commands, "mark-ready")
    ready.add_argument("--feature-id", required=True)
    phase = _campaign_command(commands, "set-feature-phase")
    phase.add_argument("--feature-id", required=True)
    phase.add_argument(
        "--phase",
        required=True,
        choices=[item.value for item in FeaturePhase],
    )
    transition = _campaign_command(commands, "transition")
    transition.add_argument(
        "--to",
        required=True,
        choices=[item.value for item in CampaignState],
    )
    _campaign_command(commands, "authorize-preflight")
    freeze = _campaign_command(commands, "freeze")
    freeze.add_argument("--approval", required=True)
    freeze.add_argument("--actor", required=True)
    _campaign_command(commands, "record-build")
    _campaign_command(commands, "record-installation")
    _campaign_command(commands, "record-case")
    _campaign_command(commands, "record-failure")
    lease = _campaign_command(commands, "grant-device-lease")
    lease.add_argument("--feature-id", required=True)
    device = _campaign_command(commands, "record-device-result")
    device.add_argument("--feature-id", required=True)
    _campaign_command(commands, "prepare-follow-up")
    _campaign_command(commands, "finalize-testing")
    invalidate = _campaign_command(commands, "invalidate-freeze")
    invalidate.add_argument("--reason", required=True)
    evidence = _campaign_command(commands, "attach-evidence")
    evidence.add_argument("--name", required=True)
    _campaign_command(commands, "status")
    return result


def payload() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("stdin JSON must be an object")
    return value


def status(campaign: Campaign) -> dict[str, Any]:
    counts = {name: 0 for name in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")}
    for case in campaign.device_cases.values():
        counts[str(case["result"])] += 1
    return {
        "campaign_id": campaign.campaign_id,
        "state": campaign.state,
        "features": len(campaign.features),
        "build_attempts": len(campaign.build_attempts),
        "device_matrix": counts,
        "failures": len(campaign.failures),
        "installed_build_id": (
            campaign.installation["build_id"] if campaign.installation else None
        ),
        "active_device_lease": campaign.active_device_lease,
    }


def _create(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.create(args.campaign_id))


def _register(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.register_feature(args.campaign_id, payload(), args.sender))


def _mark_ready(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(
        service.mark_feature_ready(args.campaign_id, args.feature_id, payload())
    )


def _set_phase(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(
        service.set_feature_phase(
            args.campaign_id,
            args.feature_id,
            FeaturePhase(args.phase),
        )
    )


def _transition(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.transition(args.campaign_id, CampaignState(args.to)))


def _authorize(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    digest = service.authorize_preflight(
        args.campaign_id,
        str(payload()["command"]),
    )
    return {"command_sha256": digest}


def _freeze(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(
        service.freeze(
            args.campaign_id,
            payload(),
            args.approval,
            actor=args.actor,
        )
    )


def _record_build(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.record_build_result(args.campaign_id, payload()))


def _record_installation(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.record_installation(args.campaign_id, payload()))


def _record_case(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.record_case(args.campaign_id, payload()))


def _record_failure(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.record_failure(args.campaign_id, payload()))


def _grant_lease(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.grant_device_lease(args.campaign_id, args.feature_id))


def _record_device_result(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(
        service.record_device_result(args.campaign_id, args.feature_id, payload())
    )


def _prepare_follow_up(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.prepare_follow_up(args.campaign_id))


def _finalize_testing(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.finalize_testing(args.campaign_id))


def _invalidate_freeze(
    args: argparse.Namespace,
    service: CampaignService,
    _: CampaignStore,
) -> dict[str, Any]:
    return status(service.invalidate_freeze(args.campaign_id, args.reason))


def _attach_evidence(
    args: argparse.Namespace,
    _: CampaignService,
    store: CampaignStore,
) -> dict[str, Any]:
    relative = store.write_evidence(
        args.campaign_id,
        args.name,
        sys.stdin.buffer.read(),
    )
    return {"evidence": relative}


def _status(
    args: argparse.Namespace,
    _: CampaignService,
    store: CampaignStore,
) -> dict[str, Any]:
    return status(store.load(args.campaign_id))


HANDLERS: dict[str, Handler] = {
    "create": _create,
    "register-feature": _register,
    "mark-ready": _mark_ready,
    "set-feature-phase": _set_phase,
    "transition": _transition,
    "authorize-preflight": _authorize,
    "freeze": _freeze,
    "record-build": _record_build,
    "record-installation": _record_installation,
    "record-case": _record_case,
    "record-failure": _record_failure,
    "grant-device-lease": _grant_lease,
    "record-device-result": _record_device_result,
    "prepare-follow-up": _prepare_follow_up,
    "finalize-testing": _finalize_testing,
    "invalidate-freeze": _invalidate_freeze,
    "attach-evidence": _attach_evidence,
    "status": _status,
}


def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = CampaignStore(campaign_root())
    result = HANDLERS[args.command](args, CampaignService(store), store)
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except (
        KeyError,
        TypeError,
        ValueError,
        FileNotFoundError,
    ) as error:
        print(f"campaign error: {error}", file=sys.stderr)
        return 2

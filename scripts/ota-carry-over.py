#!/usr/bin/env python3
"""Carry live OTA channels into a new release context, or verify their routes afterwards."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yrrp_ota.carry_over import CarryOverError, carry_over, verify_routes  # noqa: E402
from yrrp_ota.channel import Channel  # noqa: E402


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    carry = commands.add_parser("carry", help="copy and verify every live channel except --channel")
    carry.add_argument("--container", required=True)
    carry.add_argument("--channel", required=True)
    carry.add_argument("--context", type=Path, required=True)
    verify = commands.add_parser("verify-routes", help="check a candidate serves carried routes unchanged")
    verify.add_argument("--container", required=True)
    verify.add_argument("--snapshot", type=Path, required=True)
    return parser.parse_args(argv)


def carry(args: argparse.Namespace) -> None:
    """Write carried.json into the context and print one LABEL=BUILD_ID line per carried channel."""
    result = carry_over(args.container, Channel.parse(args.channel), args.context)
    snapshot = {"carried": {c.name: b for c, b in result.carried.items()}, "routes": result.snapshot}
    (args.context / "carried.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    for channel, build_id in sorted(result.carried.items(), key=lambda item: item[0].name):
        print(f"carried {channel.name} {build_id}", file=sys.stderr)
    for label, build_id in sorted(result.labels.items()):
        print(f"{label}={build_id}")


def verify(args: argparse.Namespace) -> None:
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    verify_routes(args.container, snapshot["routes"])
    print(f"carried routes unchanged: {len(snapshot['routes'])}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        if args.command == "carry":
            carry(args)
        else:
            verify(args)
    except (CarryOverError, ValueError, OSError, KeyError, TypeError) as error:
        print(f"ota-carry-over: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

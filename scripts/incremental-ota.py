#!/usr/bin/env python3
"""CLI over yrrp_ota.incremental, called by generate-incremental-ota.sh."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yrrp_ota.archive import write_json  # noqa: E402
from yrrp_ota.channel import Channel  # noqa: E402
from yrrp_ota.incremental import check_source, verify_output  # noqa: E402


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check incremental OTA sources and outputs")
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("check-source")
    source.add_argument("--target-files", type=Path, required=True)
    source.add_argument("--release-json", type=Path, required=True)
    output = commands.add_parser("verify-output")
    output.add_argument("--incremental", type=Path, required=True)
    output.add_argument("--target-files", type=Path, required=True)
    output.add_argument("--source-build", required=True)
    output.add_argument("--target-build", required=True)
    output.add_argument("--source-incremental", required=True)
    output.add_argument("--source-sha256", required=True)
    output.add_argument("--channel", default="salami/vanilla")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    if args.command == "check-source":
        result = check_source(args.target_files, args.release_json)
        print(f"{result['source_incremental']} {result['source_target_files_sha256']}")
        return
    meta = verify_output(
        args.incremental,
        args.target_files,
        source_build_id=args.source_build,
        target_build_id=args.target_build,
        source_incremental=args.source_incremental,
        source_target_files_sha256=args.source_sha256,
        channel=Channel.parse(args.channel),
    )
    write_json(args.incremental.with_name(args.incremental.name + ".json"), meta)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except (OSError, ValueError, KeyError) as error:
        print(f"incremental-ota: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

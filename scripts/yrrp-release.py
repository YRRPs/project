#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from yrrp_release.launcher import (
    ReleaseError,
    launch_release,
    parse_repository_arguments,
    prepare_release,
    validate_sha,
    validate_sha256,
)
from yrrp_release.receipt import write_receipt


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare, launch, or receipt one stateless YRRP release")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    launch = commands.add_parser("launch")
    for command in (prepare, launch):
        command.add_argument("--project-sha", required=True)
        command.add_argument("--repo", action="append", default=[], metavar="PATH=SHA")
    launch.add_argument("--approval", required=True)
    launch.add_argument("--manifest-sha256", required=True)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--input", required=True, metavar="JSON-FILE-OR--")
    return parser.parse_args(argv)


def run(arguments: argparse.Namespace) -> dict:
    if arguments.command == "receipt":
        document = _load_receipt_input(arguments.input)
        return {"receipt": str(write_receipt(document))}
    project_sha = validate_sha(arguments.project_sha, "project SHA")
    repositories = parse_repository_arguments(arguments.repo)
    if arguments.command == "prepare":
        return prepare_release(project_sha, repositories)
    manifest_sha256 = validate_sha256(arguments.manifest_sha256, "manifest SHA-256")
    return launch_release(
        arguments.approval, project_sha, repositories, manifest_sha256
    )


def _load_receipt_input(source: str) -> dict:
    if source == "-":
        document = json.load(sys.stdin)
    else:
        input_path = Path(source)
        with input_path.open(encoding="utf-8") as input_file:
            document = json.load(input_file)
    if not isinstance(document, dict):
        raise TypeError("receipt input must be a JSON object")
    return document


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (OSError, ReleaseError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"release error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

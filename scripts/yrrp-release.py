#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from yrrp_release.checks import start_check
from yrrp_release.jobs import JobProtocolError, wait_for_job
from yrrp_release.launcher import (
    ReleaseError,
    launch_release,
    parse_repository_arguments,
    prepare_release,
    validate_sha,
    validate_sha256,
)
from yrrp_release.receipt import write_receipt
from yrrp_release.targets import list_targets


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare, launch, or receipt one stateless YRRP release")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    launch = commands.add_parser("launch")
    check = commands.add_parser("check")
    for command in (prepare, launch, check):
        command.add_argument("--project-sha", required=True)
        command.add_argument("--repo", action="append", default=[], metavar="PATH=SHA")
    check.add_argument("--workspace", required=True, choices=("isolated", "release"))
    check.add_argument("--device", required=True)
    check.add_argument("--target", action="append", default=[])
    launch.add_argument("--approval", required=True)
    launch.add_argument("--manifest-sha256", required=True)
    launch.add_argument("--channel", required=True, metavar="DEVICE/TYPE")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--input", required=True, metavar="JSON-FILE-OR--")
    commands.add_parser("targets")
    wait = commands.add_parser("wait")
    wait.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def run(arguments: argparse.Namespace) -> dict:
    if arguments.command == "targets":
        return list_targets()
    if arguments.command == "receipt":
        document = _load_receipt_input(arguments.input)
        return {"receipt": str(write_receipt(document))}
    project_sha = validate_sha(arguments.project_sha, "project SHA")
    repositories = parse_repository_arguments(arguments.repo)
    if arguments.command == "prepare":
        return prepare_release(project_sha, repositories)
    if arguments.command == "check":
        return start_check(
            workspace=arguments.workspace,
            project_sha=project_sha,
            repositories=repositories,
            device=arguments.device,
            target_ids=arguments.target,
        )
    manifest_sha256 = validate_sha256(arguments.manifest_sha256, "manifest SHA-256")
    return launch_release(
        arguments.approval,
        project_sha,
        repositories,
        manifest_sha256,
        channel=arguments.channel,
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
        arguments = parse_args(argv)
        if arguments.command == "wait":
            return wait_for_job(
                arguments.run_id,
                emit=lambda event: print(
                    json.dumps(event, sort_keys=True), flush=True
                ),
            )
        result = run(arguments)
    except (
        JobProtocolError,
        OSError,
        ReleaseError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(f"release error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

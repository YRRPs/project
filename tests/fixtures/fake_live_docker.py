#!/usr/bin/env python3
"""Docker stub reporting one live OTA release container from FAKE_LIVE_* variables.

FAKE_INSPECT_FAIL makes any inspect whose arguments contain that text exit 1.
FAKE_LIVE_LABELS, when set, is printed verbatim for a `{{json .Config.Labels}}` inspect;
otherwise the legacy label pair is built from FAKE_LIVE_DEVICE and FAKE_LIVE_BUILD.
FAKE_EXEC_LOG, when set, receives the path of each `exec ... cat <path>`.
"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
live_build = os.environ.get("FAKE_LIVE_BUILD", "")
if args[:2] == ["container", "inspect"]:
    sys.exit(0 if live_build else 1)
if args[:1] == ["inspect"] and "{{json .Config.Labels}}" in args:
    if not live_build and "FAKE_LIVE_LABELS" not in os.environ:
        sys.exit(1)
    labels = os.environ.get("FAKE_LIVE_LABELS")
    print(labels if labels is not None else json.dumps({
        "io.yrrp.ota.device": os.environ.get("FAKE_LIVE_DEVICE", "salami"),
        "io.yrrp.ota.build-id": live_build,
    }))
    sys.exit(0)
if args[:1] == ["inspect"]:
    if not live_build:
        sys.exit(1)
    joined = " ".join(args)
    failing = os.environ.get("FAKE_INSPECT_FAIL", "")
    if failing and failing in joined:
        sys.exit(1)
    if "io.yrrp.ota.device" in joined:
        print(os.environ.get("FAKE_LIVE_DEVICE", "salami"))
    elif "io.yrrp.ota.build-id" in joined:
        print(live_build)
    sys.exit(0)
if args[:1] == ["exec"] and len(args) >= 2 and args[-2] == "cat":
    if "FAKE_EXEC_LOG" in os.environ:
        with open(os.environ["FAKE_EXEC_LOG"], "a") as log:
            log.write(args[-1] + "\n")
    sys.stdout.write(Path(os.environ["FAKE_RELEASE_JSON"]).read_text())
sys.exit(0)

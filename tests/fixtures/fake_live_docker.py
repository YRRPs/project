#!/usr/bin/env python3
"""Docker stub reporting one live OTA release container from FAKE_LIVE_* variables.

FAKE_INSPECT_FAIL makes any inspect whose arguments contain that text exit 1.
FAKE_LIVE_LABELS, when set, is printed verbatim for a `{{json .Config.Labels}}` inspect;
otherwise the legacy label pair is built from FAKE_LIVE_DEVICE and FAKE_LIVE_BUILD,
and with neither set the container has no labels and `null` is printed.
FAKE_EXEC_LOG, when set, receives the path of each `exec ... cat <path>`.
FAKE_DOCKER_CALL_LOG, when set, receives every call's arguments as one JSON line.
FAKE_PULL_FAIL makes `pull` exit 1.
`run --rm --entrypoint grep IMAGE ARGS... FILE` runs the real grep with ARGS over
FAKE_BASE_NGINX_CONF (default: a config serving every channel) instead of FILE;
FAKE_BASE_RUN_EXIT makes such a run exit with that status instead.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_BASE_NGINX_CONF = "location = /updates/salami.json {\nlocation = /updates/salami/gapps.json {\n"

args = sys.argv[1:]
if "FAKE_DOCKER_CALL_LOG" in os.environ:
    with open(os.environ["FAKE_DOCKER_CALL_LOG"], "a") as call_log:
        call_log.write(json.dumps(args) + "\n")
if args[:1] == ["pull"]:
    sys.exit(1 if os.environ.get("FAKE_PULL_FAIL") else 0)
if args[:4] == ["run", "--rm", "--entrypoint", "grep"]:
    if os.environ.get("FAKE_BASE_RUN_EXIT"):
        sys.exit(int(os.environ["FAKE_BASE_RUN_EXIT"]))
    conf = os.environ.get("FAKE_BASE_NGINX_CONF", DEFAULT_BASE_NGINX_CONF)
    sys.exit(subprocess.run(["grep", *args[5:-1]], input=conf, text=True).returncode)
live_build = os.environ.get("FAKE_LIVE_BUILD", "")
failing = os.environ.get("FAKE_INSPECT_FAIL", "")
inspect_fails = bool(failing) and failing in " ".join(args)
if args[:2] == ["container", "inspect"]:
    sys.exit(0 if live_build else 1)
if args[:1] == ["inspect"] and "{{json .Config.Labels}}" in args:
    if inspect_fails:
        sys.exit(1)
    labels = os.environ.get("FAKE_LIVE_LABELS")
    if labels is None and live_build:
        labels = json.dumps({
            "io.yrrp.ota.device": os.environ.get("FAKE_LIVE_DEVICE", "salami"),
            "io.yrrp.ota.build-id": live_build,
        })
    print(labels if labels is not None else "null")  # real docker prints null for no labels
    sys.exit(0)
if args[:1] == ["inspect"]:
    if not live_build:
        sys.exit(1)
    joined = " ".join(args)
    if inspect_fails:
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

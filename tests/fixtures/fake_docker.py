#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["FAKE_DOCKER_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")

mode = os.environ.get("FAKE_DOCKER_MODE", "initial")
state_path = Path(os.environ.get("FAKE_DOCKER_STATE", log.with_suffix(".state")))
base_digest = "ghcr.io/yrrps/ota-server@sha256:" + "b" * 64

if args[:1] == ["version"] or args[:2] in (["buildx", "version"], ["compose", "version"]):
    print("fake docker")
elif args[:2] == ["network", "inspect"]:
    print("[]")
elif args[:1] == ["pull"]:
    print("pulled")
elif args[:2] == ["image", "inspect"] and "RepoDigests" in " ".join(args):
    print(base_digest)
elif args[:2] == ["image", "inspect"] and "io.yrrp.ota.release" in " ".join(args):
    print("true")
elif args[:2] == ["image", "inspect"] and "{{.Id}}" in args:
    print("sha256:candidate-image")
elif args[:2] == ["container", "inspect"]:
    if mode == "initial":
        sys.exit(1)
    if args[-1].endswith(".previous") and (
        not state_path.exists() or state_path.read_text().strip() != "previous-renamed"
    ):
        sys.exit(1)
    print("[]")
elif args[:1] == ["inspect"] and "io.yrrp.ota.build-id" in " ".join(args):
    print("20990101-000000")
elif args[:1] == ["inspect"] and "io.yrrp.ota.release" in " ".join(args):
    print("false" if mode == "foreign" else "true")
elif args[:1] == ["inspect"] and "{{.Image}}" in args:
    print("sha256:previous-image")
elif args[:1] == ["inspect"] and "Health.Status" in " ".join(args):
    restored = state_path.exists() and state_path.read_text().strip() == "restored"
    if mode == "unhealthy" and not restored:
        print("unhealthy")
    else:
        print("healthy")
elif args[:1] == ["build"]:
    print("built")
elif args[:1] == ["run"]:
    if mode == "run_fail":
        sys.exit(42)
    print("candidate-container")
elif args[:1] == ["logs"]:
    print("synthetic candidate log")
elif args[:1] == ["ps"]:
    pass
elif args[:1] == ["rename"]:
    if args[-1].endswith(".previous"):
        state_path.write_text("previous-renamed\n", encoding="utf-8")
        if mode == "rename_signal":
            os.kill(os.getppid(), signal.SIGTERM)
    else:
        state_path.write_text("restored\n", encoding="utf-8")
elif args[:1] == ["start"]:
    state_path.write_text("restored\n", encoding="utf-8")
elif args[:1] == ["exec"]:
    joined = " ".join(args)
    if mode == "metadata_fail" and "/updates/salami.json" in joined:
        sys.exit(8)
    if mode == "route_missing" and "/updates/salami/1.json" in joined:
        sys.exit(8)
elif args[:1] == ["rm"]:
    if mode == "cleanup_signal" and args[-1].endswith(".previous"):
        state_path.write_text("previous-removed\n", encoding="utf-8")
        os.kill(os.getppid(), signal.SIGTERM)
elif args[:1] == ["stop"]:
    pass
elif args[:2] == ["image", "rm"]:
    pass
else:
    print(f"unhandled fake docker command: {args}", file=sys.stderr)
    sys.exit(2)

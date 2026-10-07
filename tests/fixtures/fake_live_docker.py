#!/usr/bin/env python3
"""Docker stub reporting one live OTA release container from FAKE_LIVE_* variables."""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
live_build = os.environ.get("FAKE_LIVE_BUILD", "")
if args[:2] == ["container", "inspect"]:
    sys.exit(0 if live_build else 1)
if args[:1] == ["inspect"]:
    if not live_build:
        sys.exit(1)
    joined = " ".join(args)
    if "io.yrrp.ota.device" in joined:
        print(os.environ.get("FAKE_LIVE_DEVICE", "salami"))
    elif "io.yrrp.ota.build-id" in joined:
        print(live_build)
    sys.exit(0)
if args[:1] == ["exec"] and len(args) >= 2 and args[-2] == "cat":
    sys.stdout.write(Path(os.environ["FAKE_RELEASE_JSON"]).read_text())
sys.exit(0)

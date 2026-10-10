#!/usr/bin/env python3
"""Docker stub for carry-over: live labels and files from env, candidate routes from a tree.

FAKE_LIVE_LABELS    text printed for a `{{json .Config.Labels}}` inspect (default `null`,
                    which is what real docker prints for a container without labels)
FAKE_INSPECT_FAIL   when set, that inspect exits 1 as for a missing container
FAKE_LIVE_ROOT      directory standing in for the live container's /srv/ota
FAKE_CANDIDATE_ROOT directory standing in for the candidate's /srv/ota

Like real `docker cp`, a symlink is copied as a symlink and a directory as a directory.
"""
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["inspect"] and "{{json .Config.Labels}}" in args:
    if os.environ.get("FAKE_INSPECT_FAIL"):
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    print(os.environ.get("FAKE_LIVE_LABELS", "null"))
    sys.exit(0)
if args[:1] == ["cp"]:
    source, destination = args[1], Path(args[2])
    path = source.split(":", 1)[1].removeprefix("/srv/ota/")
    origin = Path(os.environ["FAKE_LIVE_ROOT"]) / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if origin.is_symlink():
        os.symlink(os.readlink(origin), destination)
    elif origin.is_dir():
        shutil.copytree(origin, destination, symlinks=True)
    elif origin.is_file():
        shutil.copyfile(origin, destination)
    else:
        print(f"no such file: {source}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if args[:1] == ["exec"] and "wget" in args:
    url = args[-1]
    path = url.split("://", 1)[1].split("/", 1)[1]
    target = Path(os.environ["FAKE_CANDIDATE_ROOT"]) / path
    if not target.is_file():
        sys.exit(8)
    sys.stdout.buffer.write(target.read_bytes())
    sys.exit(0)
print(f"unhandled fake docker command: {args}", file=sys.stderr)
sys.exit(2)

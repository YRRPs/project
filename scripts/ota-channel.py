#!/usr/bin/env python3
"""Print one channel-derived name, path, or label for shell scripts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yrrp_ota.channel import Channel, live_channels  # noqa: E402


def live_build(channel: Channel, labels_json: str) -> str:
    """Build ID this channel serves according to a container's labels, or empty if none."""
    try:
        labels = json.loads(labels_json or "{}")
    except json.JSONDecodeError as error:
        raise ValueError(f"labels are not valid JSON: {error}") from error
    if not isinstance(labels, dict):
        raise ValueError("labels must be a JSON object")
    return live_channels(labels).get(channel, "")


# field -> (number of values it takes, function(channel, *values) -> text)
FIELDS = {
    "type": (0, lambda c: c.type),
    "device": (0, lambda c: c.device),
    "label": (0, lambda c: c.label),
    "updates-full": (0, lambda c: c.updates_full_path),
    "updates-incremental": (1, Channel.updates_incremental_path),
    "target-files": (1, Channel.target_files_name),
    "ota": (1, Channel.full_ota_name),
    "checksums": (1, Channel.checksums_name),
    "install-dir": (1, Channel.install_dir),
    "incremental": (2, Channel.incremental_ota_name),
    "live-build": (1, live_build),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True, help="DEVICE/TYPE, for example salami/gapps")
    parser.add_argument("field", choices=sorted(FIELDS))
    parser.add_argument("values", nargs="*")
    args = parser.parse_args(argv)
    arity, render = FIELDS[args.field]
    try:
        if len(args.values) != arity:
            raise ValueError(f"{args.field} takes {arity} value(s), got {len(args.values)}")
        print(render(Channel.parse(args.channel), *args.values))
    except ValueError as error:
        print(f"ota-channel: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

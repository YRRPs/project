from __future__ import annotations

import shlex
from pathlib import Path


def is_product_build_command(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True
    words = {
        word.strip(";&|()")
        for token in tokens
        for word in shlex.split(token, posix=True)
    }
    if {"mka", "brunch"}.intersection(words):
        return True
    return any(
        Path(word).name == "sign-lineage-build.sh"
        for word in words
    )

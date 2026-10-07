from __future__ import annotations

import re
import shlex
from pathlib import Path

SHELL_SYNTAX = re.compile(r"[;&|`$()\n]")
SAFE_ARGUMENT = re.compile(r"^[A-Za-z0-9_./:+,=@%-]+$")
SIMPLE_READ_ONLY = {"cat", "tail", "head", "ls", "stat", "wc", "grep"}


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def builder_remote_tokens(command: str) -> list[str] | None:
    tokens = _tokens(command)
    if "ssh" not in tokens or "AndroidBuilder" not in tokens:
        return None
    host_index = tokens.index("AndroidBuilder")
    remote = " ".join(tokens[host_index + 1 :])
    if not remote or SHELL_SYNTAX.search(remote):
        return None
    nested = _tokens(remote)
    if not nested or not all(SAFE_ARGUMENT.fullmatch(item) for item in nested):
        return None
    return nested


def is_supported_preflight_command(command: str) -> bool:
    remote = builder_remote_tokens(command)
    if remote is None or len(remote) < 2:
        return False
    return remote[0] in {"m", "atest"}


def is_read_only_builder_command(command: str) -> bool:
    remote = builder_remote_tokens(command)
    if remote is None:
        return False
    if remote[0] in SIMPLE_READ_ONLY:
        return True
    if remote[:2] in (["git", "status"], ["git", "rev-parse"]):
        return True
    if remote[:2] == ["screen", "-list"]:
        return True
    return remote[:2] == ["docker", "inspect"]


def is_product_build_command(command: str) -> bool:
    remote = builder_remote_tokens(command)
    if remote is not None and remote[0] in {"mka", "brunch"}:
        return True
    return any(
        Path(token.strip(";&|()")).name == "sign-lineage-build.sh"
        for token in _tokens(command)
    )

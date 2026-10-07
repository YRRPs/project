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


BUILD_ID = re.compile(r"^[0-9]{8}-[0-9]{6}$")
SIGNED_PREFIX = "/opt/android/out/signed/lineage-23.2-salami-"
GENERATE_INCREMENTAL = "/opt/yrrp/project/scripts/generate-incremental-ota.sh"
DEPLOY_RELEASE = "/opt/yrrp/project/scripts/deploy-ota-release.sh"


def _options(tokens: list[str]) -> dict[str, str] | None:
    if len(tokens) % 2:
        return None
    options = dict(zip(tokens[0::2], tokens[1::2]))
    return options if len(options) == len(tokens) // 2 else None


def _is_generate_recovery(options: dict[str, str]) -> bool:
    return set(options) == {"--source-build", "--target-build"} and all(
        BUILD_ID.fullmatch(value) for value in options.values()
    )


def _is_deploy_recovery(options: dict[str, str]) -> bool:
    build_id = options.get("--build-id", "")
    if not BUILD_ID.fullmatch(build_id):
        return False
    if options.get("--ota") != f"{SIGNED_PREFIX}{build_id}-signed-ota.zip":
        return False
    if options.get("--target-files") != f"{SIGNED_PREFIX}{build_id}-signed-target_files.zip":
        return False
    incremental = options.get("--incremental")
    if incremental is None:
        return len(options) == 3
    pattern = re.escape(SIGNED_PREFIX) + r"[0-9]{8}-[0-9]{6}-to-" + re.escape(build_id) + r"-signed-incremental-ota\.zip"
    return len(options) == 4 and re.fullmatch(pattern, incremental) is not None


def is_supported_recovery_command(command: str) -> bool:
    """Accept only the exact builder reruns the build campaign may authorize once."""
    remote = builder_remote_tokens(command)
    if not remote:
        return False
    options = _options(remote[1:])
    if options is None:
        return False
    if remote[0] == GENERATE_INCREMENTAL:
        return _is_generate_recovery(options)
    if remote[0] == DEPLOY_RELEASE:
        return _is_deploy_recovery(options)
    return False

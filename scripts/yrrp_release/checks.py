from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .jobs import parse_run_id, paths_for_run
from .launcher import validate_repo_path, validate_sha, validate_sha256
from .targets import parse_targets, validate_device

ATEST_SUMMARY_PATTERN = re.compile(
    r"^[^:\n]+: Passed: (?P<passed>[0-9]+), Failed: (?P<failed>[0-9]+), "
    r"Ignored: (?P<ignored>[0-9]+)$",
    re.MULTILINE,
)
CHECK_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "workspace",
        "device",
        "source",
        "targets",
        "started_at",
        "completed_at",
        "log",
        "result",
        "exit_code",
        "failure_reason",
    }
)
SOURCE_KEYS = frozenset({"project_sha", "repositories", "manifest_sha256"})
TARGET_KEYS = frozenset(
    {"ids", "executor", "argv", "result", "exit_code", "started_at", "completed_at"}
)


def parse_atest_result(output: str) -> int:
    matches = list(ATEST_SUMMARY_PATTERN.finditer(output))
    if len(matches) != 1:
        raise ValueError("atest output must contain exactly one execution summary")
    values = (int(matches[0].group(name)) for name in ("passed", "failed", "ignored"))
    count = sum(values)
    if count <= 0:
        raise ValueError("atest executed zero tests")
    return count


def validate_check_result(document: object) -> dict[str, object]:
    check = _mapping(document, "check result")
    _require_keys(check, CHECK_KEYS, "check result")
    if check["schema_version"] != 1:
        raise ValueError("check result has unsupported schema version")
    run_id = _text(check["run_id"], "check result.run_id")
    if parse_run_id(run_id) != "check":
        raise ValueError("check result run ID must identify a check")
    if check["workspace"] not in ("isolated", "release"):
        raise ValueError("check result has unsupported workspace")
    validate_device(_text(check["device"], "check result.device"))
    source = _validate_source(check["source"])
    targets = _validate_targets(check["targets"])
    started = _timestamp(check["started_at"], "check result.started_at")
    completed = _timestamp(check["completed_at"], "check result.completed_at")
    if completed < started:
        raise ValueError("check result completes before it starts")
    expected_log = str(paths_for_run(run_id).log)
    if check["log"] != expected_log:
        raise ValueError("check result log path does not match run ID")
    result = _result(check["result"], "check result.result")
    exit_code = _exit_code(check["exit_code"], "check result.exit_code")
    failure_reason = check["failure_reason"]
    _validate_outcome(result, exit_code, failure_reason, targets)
    validated = copy.deepcopy(check)
    validated["source"] = source
    validated["targets"] = targets
    return validated


def match_check_to_source(
    check: object,
    prepared_source: dict[str, object],
    required_targets: Sequence[str],
) -> dict[str, object]:
    if not required_targets:
        raise ValueError("at least one required target is needed")
    required = tuple(target.id for target in parse_targets(list(required_targets)))
    validated = validate_check_result(check)
    if validated["workspace"] != "release":
        raise ValueError("only release-workspace checks satisfy preparation")
    if validated["result"] != "passed":
        raise ValueError("required check did not pass")
    expected_source = _validate_source(prepared_source)
    if validated["source"] != expected_source:
        raise ValueError("required check source does not match prepared source")
    completed = {
        target_id
        for target in validated["targets"]
        if target["result"] == "passed"
        for target_id in target["ids"]
    }
    missing = [target_id for target_id in required if target_id not in completed]
    if missing:
        raise ValueError("required check is missing targets: " + ", ".join(missing))
    return validated


def _validate_source(value: object) -> dict[str, object]:
    source = _mapping(value, "check source")
    _require_keys(source, SOURCE_KEYS, "check source")
    project_sha = validate_sha(_text(source["project_sha"], "check source.project_sha"), "project SHA")
    manifest_sha256 = validate_sha256(
        _text(source["manifest_sha256"], "check source.manifest_sha256"),
        "manifest SHA-256",
    )
    repositories = _mapping(source["repositories"], "check source.repositories")
    validated_repositories: dict[str, str] = {}
    for path, sha in repositories.items():
        if not isinstance(path, str):
            raise TypeError("check source repository paths must be strings")
        validated_path = validate_repo_path(path)
        validated_repositories[validated_path] = validate_sha(
            _text(sha, f"check source.repositories[{path!r}]"),
            f"repository SHA for {path}",
        )
    return {
        "project_sha": project_sha,
        "repositories": dict(sorted(validated_repositories.items())),
        "manifest_sha256": manifest_sha256,
    }


def _validate_targets(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise TypeError("check result.targets must be a non-empty list")
    seen: set[str] = set()
    validated: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        target = _mapping(raw, f"check result.targets[{index}]")
        executor = _text(target.get("executor"), f"check result.targets[{index}].executor")
        expected_keys = TARGET_KEYS | ({"executed_tests"} if executor == "atest" else set())
        _require_keys(target, frozenset(expected_keys), f"check result.targets[{index}]")
        validated_target = _validate_target(target, executor, index)
        for target_id in validated_target["ids"]:
            if target_id in seen:
                raise ValueError(f"duplicate check target ID: {target_id}")
            seen.add(target_id)
        validated.append(validated_target)
    return validated


def _validate_target(
    target: dict[str, Any], executor: str, index: int
) -> dict[str, object]:
    label = f"check result.targets[{index}]"
    ids_value = target["ids"]
    if not isinstance(ids_value, list) or not ids_value:
        raise TypeError(f"{label}.ids must be a non-empty list")
    if not all(isinstance(item, str) for item in ids_value):
        raise TypeError(f"{label}.ids must contain strings")
    parsed = parse_targets(ids_value)
    if any(item.executor != executor for item in parsed):
        raise ValueError(f"{label} executor does not match target IDs")
    if executor == "m":
        expected_argv = ["m", *(item.argument for item in parsed)]
    elif executor == "atest" and len(parsed) == 1:
        expected_argv = ["atest", parsed[0].argument]
    else:
        raise ValueError(f"{label} has unsupported executor or target count")
    if target["argv"] != expected_argv:
        raise ValueError(f"{label}.argv does not match fixed target registry")
    result = _result(target["result"], f"{label}.result")
    exit_code = _exit_code(target["exit_code"], f"{label}.exit_code")
    if result == "passed" and exit_code != 0:
        raise ValueError(f"{label} passed with nonzero exit code")
    if result == "failed" and exit_code == 0:
        raise ValueError(f"{label} failed with zero exit code")
    started = _timestamp(target["started_at"], f"{label}.started_at")
    completed = _timestamp(target["completed_at"], f"{label}.completed_at")
    if completed < started:
        raise ValueError(f"{label} completes before it starts")
    validated = copy.deepcopy(target)
    if executor == "atest":
        executed = _exit_code(target["executed_tests"], f"{label}.executed_tests")
        if executed < 0 or (result == "passed" and executed == 0):
            raise ValueError(f"{label} has invalid executed test count")
        validated["executed_tests"] = executed
    return validated


def _validate_outcome(
    result: str,
    exit_code: int,
    failure_reason: object,
    targets: list[dict[str, object]],
) -> None:
    all_passed = all(target["result"] == "passed" for target in targets)
    if result == "passed":
        if exit_code != 0 or not all_passed or failure_reason is not None:
            raise ValueError("passed check result has inconsistent outcome evidence")
        return
    if exit_code == 0 or all_passed:
        raise ValueError("failed check result has inconsistent outcome evidence")
    if not isinstance(failure_reason, str) or not failure_reason.strip():
        raise TypeError("failed check result requires a failure reason")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    if not text.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z format")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error


def _result(value: object, label: str) -> str:
    if value not in ("passed", "failed"):
        raise ValueError(f"{label} must be passed or failed")
    return str(value)


def _exit_code(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value

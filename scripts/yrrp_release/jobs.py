from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Literal, cast

from .launcher import remote_python_argv

JobKind = Literal["check", "rollback"]
RUN_ID_PATTERN = re.compile(
    r"(?P<kind>check|rollback)-(?P<stamp>[0-9]{8}-[0-9]{6})-"
    r"(?P<random>[0-9a-f]{8})"
)
ROOTS: dict[JobKind, PurePosixPath] = {
    "check": PurePosixPath("/opt/android/out/yrrp-checks"),
    "rollback": PurePosixPath("/opt/android/out/yrrp-rollbacks"),
}
REMOTE_WAIT_SCRIPT = r'''
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

pattern = re.compile(
    r"(?P<kind>check|rollback)-(?P<stamp>[0-9]{8}-[0-9]{6})-"
    r"(?P<random>[0-9a-f]{8})"
)
roots = {
    "check": Path("/opt/android/out/yrrp-checks"),
    "rollback": Path("/opt/android/out/yrrp-rollbacks"),
}
payload = json.load(sys.stdin)
run_id = payload.get("run_id")
match = pattern.fullmatch(run_id) if isinstance(run_id, str) else None
if match is None:
    raise SystemExit("invalid run ID")
datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
directory = roots[match.group("kind")] / run_id
status_path = directory / "status.json"
result_path = directory / "result.json"
last_event = None
for _ in range(3000):
    if status_path.is_file():
        event = json.loads(status_path.read_text())
        encoded = json.dumps(event, sort_keys=True)
        if encoded != last_event:
            print(encoded, flush=True)
            last_event = encoded
    if result_path.is_file():
        result = json.loads(result_path.read_text())
        encoded = json.dumps(result, sort_keys=True)
        if encoded != last_event:
            print(encoded, flush=True)
        exit_code = result.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise SystemExit("job result has invalid exit code")
        raise SystemExit(exit_code)
    screens = subprocess.run(
        ["screen", "-list"], capture_output=True, check=False, text=True
    ).stdout
    if f".{run_id}" not in screens:
        raise SystemExit("detached job exited without final result")
    time.sleep(0.1)
raise SystemExit("timed out waiting for detached job")
'''


class JobProtocolError(RuntimeError):
    """Remote job evidence did not satisfy the fixed protocol."""


@dataclass(frozen=True)
class JobPaths:
    directory: PurePosixPath
    status: PurePosixPath
    result: PurePosixPath
    log: PurePosixPath


@dataclass(frozen=True)
class LocalJobPaths:
    directory: Path
    status: Path
    result: Path
    log: Path


def new_run_id(
    kind: JobKind,
    *,
    now: datetime | None = None,
    random_hex: str | None = None,
) -> str:
    if kind not in ROOTS:
        raise ValueError(f"unsupported job kind: {kind!r}")
    instant = now or datetime.now(timezone.utc)
    suffix = random_hex or secrets.token_hex(4)
    if not re.fullmatch(r"[0-9a-f]{8}", suffix):
        raise ValueError(
            "job random suffix must be eight lowercase hexadecimal characters"
        )
    return f"{kind}-{instant.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def parse_run_id(value: str) -> JobKind:
    match = RUN_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid run ID: {value!r}")
    try:
        datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
    except ValueError as error:
        raise ValueError(f"invalid run ID timestamp: {value!r}") from error
    return cast(JobKind, match.group("kind"))


def paths_for_run(value: str) -> JobPaths:
    kind = parse_run_id(value)
    directory = ROOTS[kind] / value
    return JobPaths(
        directory=directory,
        status=directory / "status.json",
        result=directory / "result.json",
        log=directory / "job.log",
    )


def local_paths_for_run(value: str, root: Path) -> LocalJobPaths:
    parse_run_id(value)
    directory = root / value
    return LocalJobPaths(
        directory=directory,
        status=directory / "status.json",
        result=directory / "result.json",
        log=directory / "job.log",
    )


def write_status(paths: LocalJobPaths, document: dict[str, object]) -> None:
    paths.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".status-", suffix=".tmp", dir=paths.directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(document, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(paths.status)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_result(paths: LocalJobPaths, document: dict[str, object]) -> None:
    paths.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        paths.result,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(document, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def _write_payload(stream: IO[str], run_id: str) -> None:
    json.dump({"run_id": run_id}, stream, sort_keys=True)
    stream.write("\n")
    stream.flush()
    stream.close()


def _validated_event(line: str, run_id: str) -> dict[str, object]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError as error:
        raise JobProtocolError("remote wait returned invalid JSON") from error
    if not isinstance(event, dict):
        raise JobProtocolError("remote wait event must be a JSON object")
    if event.get("run_id") != run_id:
        raise JobProtocolError("remote wait event has wrong run ID")
    if not isinstance(event.get("phase"), str) or not isinstance(
        event.get("result"), str
    ):
        raise JobProtocolError("remote wait event lacks phase or result")
    return event


def wait_for_job(
    run_id: str,
    emit: Callable[[dict[str, object]], None],
    *,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> int:
    parse_run_id(run_id)
    process = popen(
        remote_python_argv(REMOTE_WAIT_SCRIPT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None or process.stdout is None:
        raise JobProtocolError("remote wait process has no text streams")
    _write_payload(process.stdin, run_id)
    last_key: tuple[str, str] | None = None
    final_exit: int | None = None
    for line in process.stdout:
        event = _validated_event(line, run_id)
        key = (cast(str, event["phase"]), cast(str, event["result"]))
        if key != last_key:
            emit(event)
            last_key = key
        if event["result"] != "pending":
            exit_code = event.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                raise JobProtocolError("final result has invalid exit code")
            final_exit = exit_code
    process.wait()
    if final_exit is None:
        raise JobProtocolError("remote wait ended without final result")
    return final_exit

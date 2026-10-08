from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from yrrp_ota.naming import SHA256_PATTERN

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPROVAL = "Build and release"
OUTPUT_TAIL_LINES = 40
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")

PROJECT_UPDATE_SCRIPT = r'''
import subprocess
import sys
from pathlib import Path

PROJECT = Path("/project")
PROJECT_URL = "https://github.com/YRRPs/lineageos-salami-custom.git"
expected_sha = sys.argv[1]


def run(*args):
    return subprocess.run(
        list(args), cwd=PROJECT, capture_output=True, check=True, text=True
    ).stdout.strip()


if run("git", "status", "--porcelain"):
    raise SystemExit("project checkout is dirty before synchronization")
run("git", "fetch", "--no-tags", PROJECT_URL, expected_sha)
run("git", "checkout", "--detach", expected_sha)
if run("git", "rev-parse", "HEAD") != expected_sha:
    raise SystemExit("project checkout did not reach requested SHA")
if run("git", "status", "--porcelain"):
    raise SystemExit("project checkout is dirty after synchronization")
'''

_REMOTE_COMMON = "PROJECT_UPDATE_SCRIPT = " + repr(PROJECT_UPDATE_SCRIPT) + r'''
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ANDROID = Path("/opt/android")
PROJECT = Path("/opt/yrrp/project")
SIGNING = Path("/opt/yrrp/signing")
LOCK_PATH = Path("/home/android/.yrrp-build-launch.lock")
STATUS_PATH = Path("/home/android/signed-build.status")
REPO = str(ANDROID / ".repo/repo/repo")
PROJECT_HOST_PATH = "/mnt/fast/docker/android/project"
PROJECT_MOUNT = PROJECT_HOST_PATH + ":/project:rw"


def run(*args, cwd=None, check=True):
    try:
        return subprocess.run(
            list(args), cwd=cwd, capture_output=True, check=check, text=True
        )
    except subprocess.CalledProcessError as error:
        if error.stdout:
            sys.stderr.write(error.stdout)
        if error.stderr:
            sys.stderr.write(error.stderr)
        raise


def output(*args, cwd=None):
    return run(*args, cwd=cwd).stdout.strip()


def program_name(token):
    name = token.rsplit("/", 1)[-1]
    for suffix in (".bash", ".sh", ".py"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def build_process_active(processes):
    builds = {
        "soong_ui",
        "ninja",
        "ota_from_target_files",
        "sign_target_files_apks",
        "sign-lineage-build",
    }
    for line in processes.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        names = [program_name(token) for token in tokens[:2]]
        if names and names[0] in builds:
            return True
        interpreted = names[:1] and (
            names[0] in ("bash", "sh") or names[0].startswith("python")
        )
        if interpreted and len(names) == 2 and names[1] in builds:
            return True
    return False


def reject_active_release(include_screen=True):
    processes = output("ps", "-eo", "args", cwd=ANDROID)
    if build_process_active(processes):
        raise SystemExit("build or signing process is already active")
    if include_screen:
        screens = run("screen", "-list", check=False).stdout
        if ".yrrp-ota-build" in screens:
            raise SystemExit("yrrp-ota-build screen already exists")


def validate_signing_and_network():
    run("sha256sum", "--check", "--quiet", "MANIFEST.sha256", cwd=SIGNING)
    if (SIGNING / "testkey.pk8").readlink().name != "releasekey.pk8":
        raise SystemExit("testkey.pk8 does not point to releasekey.pk8")
    if (SIGNING / "testkey.x509.pem").readlink().name != "releasekey.x509.pem":
        raise SystemExit("testkey.x509.pem does not point to releasekey.x509.pem")
    run("docker", "network", "inspect", "proxy-net")


def synchronize_project(expected_sha):
    inspection = output(
        "docker", "inspect", "--format", "{{.State.Running}} {{.Image}}",
        "lineageos-builder",
    ).split()
    if len(inspection) != 2 or inspection[0] != "true":
        raise SystemExit("lineageos-builder is not running")
    image_id = inspection[1]
    run(
        "docker", "run", "--rm", "--user", "950:950",
        "-v", PROJECT_MOUNT, "--entrypoint", "python3", image_id,
        "-c", PROJECT_UPDATE_SCRIPT, expected_sha,
    )
    validate_project(expected_sha)


def validate_project(expected_sha):
    actual = output("git", "rev-parse", "HEAD", cwd=PROJECT)
    if actual != expected_sha:
        raise SystemExit(f"project SHA {actual} does not match requested {expected_sha}")
    if output("git", "status", "--porcelain", cwd=PROJECT):
        raise SystemExit("builder project checkout is dirty")


def synchronize_repositories(repositories):
    for relative, expected_sha in sorted(repositories.items()):
        run(REPO, "sync", relative, cwd=ANDROID)
        root = ANDROID / relative
        run("git", "cat-file", "-e", f"{expected_sha}^{{commit}}", cwd=root)
        run("git", "checkout", "--detach", expected_sha, cwd=root)


def local_manifest_hashes():
    directory = ANDROID / ".repo/local_manifests"
    if not directory.is_dir():
        return {}
    result = {}
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".xml":
            raise SystemExit(f"unsupported local manifest entry: {path}")
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_all_repositories_clean():
    manifests = ANDROID / ".repo/manifests"
    manifest_status = output("git", "status", "--porcelain", cwd=manifests)
    if manifest_status:
        sys.stderr.write(f"{manifests}\n{manifest_status}\n")
        raise SystemExit("repo manifest checkout is dirty")
    local_manifest_hashes()
    command = (
        'dirty=$(git status --porcelain); if [ -n "$dirty" ]; then '
        'printf "%s\\n%s\\n" "$REPO_PATH" "$dirty" >&2; exit 1; fi'
    )
    check = subprocess.run(
        [REPO, "forall", "-e", "-c", command],
        cwd=ANDROID,
        capture_output=True,
        text=True,
    )
    if check.returncode:
        sys.stderr.write(check.stderr)
        raise SystemExit("Android checkout has dirty repositories")


def capture_source(expected_project_sha, repositories, include_manifest=False):
    validate_project(expected_project_sha)
    validate_all_repositories_clean()
    manifest = output(REPO, "manifest", "-r", cwd=ANDROID)
    source = {
        "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "local_manifests": local_manifest_hashes(),
        "project_sha": output("git", "rev-parse", "HEAD", cwd=PROJECT),
        "repositories": {},
        "branches": {},
        "clean_repositories": [],
    }
    for relative in sorted(repositories):
        root = ANDROID / relative
        actual = output("git", "rev-parse", "HEAD", cwd=root)
        if actual != repositories[relative]:
            raise SystemExit(
                f"repository SHA for {relative} is {actual}, requested {repositories[relative]}"
            )
        if output("git", "status", "--porcelain", cwd=root):
            raise SystemExit(f"repository checkout is dirty: {relative}")
        source["repositories"][relative] = actual
        source["branches"][relative] = output(
            "git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root
        )
        source["clean_repositories"].append(relative)
    if include_manifest:
        source["manifest_xml"] = manifest
    return source


def acquire_nonblocking_lock():
    LOCK_PATH.touch(mode=0o600, exist_ok=True)
    lock = LOCK_PATH.open("r+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SystemExit(f"another release operation holds {LOCK_PATH}")
    return lock
'''

REMOTE_LAUNCH_WORKER_SCRIPT = _REMOTE_COMMON + r'''
def write_launch_status(value):
    STATUS_PATH.write_text(value + "\n")


def write_launch_ack(directory, document):
    if not directory.is_dir():
        return False
    acknowledgment = directory / "ack.json"
    temporary = directory / "ack.tmp"
    try:
        temporary.write_text(json.dumps(document, sort_keys=True) + "\n")
        temporary.chmod(0o600)
        temporary.replace(acknowledgment)
    except FileNotFoundError:
        return False
    return True


def failure_reason(error):
    if not isinstance(error, subprocess.CalledProcessError):
        reason = str(error).strip().replace("\n", " ")
        return reason or error.__class__.__name__
    sections = [f"exit-{error.returncode}"]
    for label, stream in (("stderr", error.stderr), ("stdout", error.stdout)):
        if isinstance(stream, bytes):
            stream = stream.decode(errors="replace")
        tail = (stream or "").strip().splitlines()[-40:]
        if tail:
            sections.append(f"{label} tail:\n" + "\n".join(tail))
    return "\n".join(sections)


def validate_signer_entry():
    signer = Path("/opt/yrrp/project/scripts/sign-lineage-build.sh")
    if not signer.is_file() or not os.access(signer, os.X_OK):
        raise SystemExit(f"signer is missing or not executable: {signer}")


def launch_worker(expected, ack_directory, signer_spawn=subprocess.Popen):
    lock = None
    try:
        lock = acquire_nonblocking_lock()
        write_launch_status("launch-validating")
        reject_active_release(include_screen=False)
        validate_signing_and_network()
        source = capture_source(expected["project_sha"], expected["repositories"])
        if source["manifest_sha256"] != expected["manifest_sha256"]:
            raise SystemExit("revision-locked manifest SHA-256 does not match approval")
        validate_signer_entry()
        if not ack_directory.is_dir():
            raise SystemExit("launch acknowledgment channel was closed")
        environment = os.environ.copy()
        environment["YRRP_BUILD_LOCK_HELD"] = "1"
        environment["YRRP_BUILD_LOCK_FD"] = str(lock.fileno())
        signer = "/opt/yrrp/project/scripts/sign-lineage-build.sh"
        write_launch_status("launch-starting-signer")
        process = signer_spawn(
            [signer], env=environment, pass_fds=(lock.fileno(),)
        )
    except BaseException as error:
        reason = failure_reason(error)
        if lock is not None:
            write_launch_status(f"launch-failed:{reason}")
        failure = {"ok": False, "error": reason}
        if expected.get("screen_log"):
            failure["log"] = expected["screen_log"]
        write_launch_ack(ack_directory, failure)
        if lock is not None:
            lock.close()
        raise
    if not write_launch_ack(ack_directory, {"ok": True, "source": source}):
        process.terminate()
        process.wait()
        write_launch_status("launch-failed:acknowledgment-channel-closed")
        return 1
    try:
        return process.wait()
    finally:
        lock.close()


expected = json.loads(sys.argv[1])
ack_directory = Path(sys.argv[2])
raise SystemExit(launch_worker(expected, ack_directory))
'''

REMOTE_PREPARE_SCRIPT = _REMOTE_COMMON + r'''
def prepare_source(payload):
    reject_active_release()
    validate_signing_and_network()
    validate_all_repositories_clean()
    synchronize_project(payload["project_sha"])
    synchronize_repositories(payload["repositories"])
    return capture_source(
        payload["project_sha"], payload["repositories"], include_manifest=True
    )


payload = json.load(sys.stdin)
with acquire_nonblocking_lock():
    source = prepare_source(payload)
print(json.dumps(source, sort_keys=True))
'''

REMOTE_LAUNCH_SCRIPT = (
    _REMOTE_COMMON
    + "REMOTE_LAUNCH_WORKER_SCRIPT = "
    + repr(REMOTE_LAUNCH_WORKER_SCRIPT)
    + r'''
import shutil
import time
import uuid


def wait_for_launch_ack(directory, log_path=None, attempts=3000):
    acknowledgment_path = directory / "ack.json"
    log_suffix = f"; screen log: {log_path}" if log_path is not None else ""

    def read_acknowledgment():
        if not acknowledgment_path.is_file():
            return None
        acknowledgment = json.loads(acknowledgment_path.read_text())
        if not acknowledgment.get("ok"):
            raise SystemExit(
                "detached release launch failed: "
                + str(acknowledgment.get("error", "unknown error"))
                + log_suffix
            )
        return acknowledgment

    try:
        for _ in range(attempts):
            acknowledgment = read_acknowledgment()
            if acknowledgment is not None:
                return acknowledgment
            screens = run("screen", "-list", check=False).stdout
            if ".yrrp-ota-build" not in screens:
                acknowledgment = read_acknowledgment()
                if acknowledgment is not None:
                    return acknowledgment
                raise SystemExit(
                    "detached release worker exited without acknowledgment" + log_suffix
                )
            time.sleep(0.1)
        raise SystemExit(
            "timed out waiting for detached release launch acknowledgment" + log_suffix
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


payload = json.load(sys.stdin)
reject_active_release()
run(
    "sudo", "install", "-d", "-m", "0777", "-o", "root", "-g", "utmp", "/run/screen"
)
output_dir = ANDROID / "out/signed"
output_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
log = output_dir / f"yrrp-ota-build-{stamp}.log"
ack_directory = Path("/home/android") / f".yrrp-launch-ack-{uuid.uuid4().hex}"
ack_directory.mkdir(mode=0o700)
worker_payload = {**payload, "screen_log": str(log)}
try:
    run(
        "screen", "-L", "-Logfile", str(log),
        "-dmS", "yrrp-ota-build",
        "python3", "-c", REMOTE_LAUNCH_WORKER_SCRIPT,
        json.dumps(worker_payload, sort_keys=True), str(ack_directory),
    )
    acknowledgment = wait_for_launch_ack(ack_directory, log_path=log)
finally:
    shutil.rmtree(ack_directory, ignore_errors=True)
print(json.dumps({"log": str(log), "source": acknowledgment["source"]}, sort_keys=True))
'''
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ReleaseError(RuntimeError):
    """A release launcher operation failed with concise remote evidence."""


def validate_sha(value: str, label: str) -> str:
    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase 40-character SHA")
    return value


def validate_sha256(value: str, label: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase 64-character SHA-256")
    return value


def validate_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    parts = path.parts
    valid = (
        bool(value)
        and not path.is_absolute()
        and len(parts) >= 1
        and value == "/".join(parts)
        and all(part not in (".", "..") for part in parts)
        and all(PATH_SEGMENT_PATTERN.fullmatch(part) for part in parts)
        and not parts[0].startswith("-")
    )
    if not valid:
        raise ValueError(f"unsafe repository path: {value!r}")
    return value


def parse_repository_arguments(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("repository must use PATH=SHA")
        raw_path, raw_sha = value.split("=", 1)
        path = validate_repo_path(raw_path)
        if path in parsed:
            raise ValueError(f"duplicate repository path: {path}")
        parsed[path] = validate_sha(raw_sha, f"repository SHA for {path}")
    return dict(sorted(parsed.items()))


def verify_local_project(expected_sha: str, root: Path = PROJECT_ROOT) -> None:
    status = _local_git(["status", "--porcelain"], root)
    if status:
        raise ValueError("local project checkout is dirty")
    actual = _local_git(["rev-parse", "HEAD"], root)
    if actual != expected_sha:
        raise ValueError(
            f"local project HEAD {actual} does not match requested {expected_sha}"
        )


def _local_git(arguments: list[str], root: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    ).stdout.strip()


def remote_python_argv(script: str) -> list[str]:
    return ["ssh", "AndroidBuilder", "python3", "-c", shlex.quote(script)]


def run_remote(
    script: str,
    payload: dict[str, Any],
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    try:
        result = runner(
            remote_python_argv(script),
            input=json.dumps(payload, sort_keys=True),
            capture_output=True,
            check=True,
            text=True,
            shell=False,
        )
    except subprocess.CalledProcessError as error:
        raise ReleaseError(_format_remote_failure(error)) from error
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ReleaseError("remote launcher returned invalid JSON") from error
    if not isinstance(document, dict):
        raise ReleaseError("remote launcher did not return a JSON object")
    return document


def _format_remote_failure(error: subprocess.CalledProcessError) -> str:
    sections = [f"remote release command failed with exit {error.returncode}"]
    for label, stream in (("stderr", error.stderr), ("stdout", error.stdout)):
        if isinstance(stream, bytes):
            stream = stream.decode(errors="replace")
        tail = (stream or "").strip().splitlines()[-OUTPUT_TAIL_LINES:]
        if tail:
            sections.append(f"remote {label} tail:\n" + "\n".join(tail))
    return "\n".join(sections)


def prepare_release(
    project_sha: str,
    repositories: dict[str, str],
) -> dict[str, Any]:
    project_sha, repositories = _validated_request(project_sha, repositories)
    verify_local_project(project_sha)
    return run_remote(
        REMOTE_PREPARE_SCRIPT,
        {"project_sha": project_sha, "repositories": repositories},
    )


def launch_release(
    approval: str,
    project_sha: str,
    repositories: dict[str, str],
    manifest_sha256: str,
) -> dict[str, Any]:
    if approval != APPROVAL:
        raise ValueError(f"launch requires exact approval {APPROVAL!r}")
    project_sha, repositories = _validated_request(project_sha, repositories)
    manifest_sha256 = validate_sha256(manifest_sha256, "manifest SHA-256")
    verify_local_project(project_sha)
    return run_remote(
        REMOTE_LAUNCH_SCRIPT,
        {
            "project_sha": project_sha,
            "repositories": repositories,
            "manifest_sha256": manifest_sha256,
        },
    )


def _validated_request(
    project_sha: str,
    repositories: dict[str, str],
) -> tuple[str, dict[str, str]]:
    validated_project = validate_sha(project_sha, "project SHA")
    arguments = [f"{path}={sha}" for path, sha in repositories.items()]
    return validated_project, parse_repository_arguments(arguments)

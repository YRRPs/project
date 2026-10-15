from __future__ import annotations

from .launcher import _REMOTE_COMMON

_REMOTE_JOB_BODY = r'''
import tempfile

ROLLBACK_ROOT = Path("/opt/android/out/yrrp-rollbacks")
SIGNED_ROOT = Path("/opt/android/out/signed")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path, document, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(document, output_file, sort_keys=True)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".status-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(document, output_file, sort_keys=True)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_status(run_id, phase, result="pending", exit_code=None, reason=None):
    document = {"run_id": run_id, "phase": phase, "result": result}
    if exit_code is not None:
        document["exit_code"] = exit_code
    if reason is not None:
        document["failure_reason"] = reason
    atomic_json(ROLLBACK_ROOT / run_id / "status.json", document)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_legacy_identity(legacy, channel, build_id):
    if not isinstance(legacy, dict):
        raise SystemExit("legacy rollback record is invalid")
    if legacy.get("channel") != channel.name or legacy.get("build_id") != build_id:
        raise SystemExit("legacy rollback identity does not match request")
    artifacts = legacy.get("artifacts")
    if not isinstance(artifacts, dict):
        raise SystemExit("legacy rollback artifacts are invalid")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(expected, dict):
            raise SystemExit("legacy rollback artifact is invalid")
        path = SIGNED_ROOT / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"legacy rollback artifact is missing: {name}")
        if path.stat().st_size != expected.get("size"):
            raise SystemExit(f"legacy rollback artifact size mismatch: {name}")
        if sha256_file(path) != expected.get("sha256"):
            raise SystemExit(f"legacy rollback artifact digest mismatch: {name}")
    return artifacts


def create_legacy_record(legacy, channel, build_id, project_sha):
    artifacts = verify_legacy_identity(legacy, channel, build_id)
    ota_name = channel.full_ota_name(build_id)
    target_name = channel.target_files_name(build_id)
    incrementals = [
        name for name in artifacts if name.endswith("-signed-incremental-ota.zip")
    ]
    record = build_deployment_record(
        channel=channel,
        build_id=build_id,
        signed_root=SIGNED_ROOT,
        ota_name=ota_name,
        target_files_name=target_name,
        incremental_name=incrementals[0] if incrementals else None,
        created_at=datetime.now(timezone.utc),
        deployer_project_sha=project_sha,
    )
    for field in ("full_ota", "target_files", "checksums", "incremental"):
        artifact = record[field]
        if artifact is None:
            continue
        expected = artifacts.get(artifact["name"])
        if expected is None or artifact["sha256"] != expected["sha256"]:
            raise SystemExit("legacy receipt does not match independently verified artifacts")
        if "size" in artifact and artifact["size"] != expected["size"]:
            raise SystemExit("legacy receipt size does not match independently verified artifacts")
    publish_deployment_record(record, SIGNED_ROOT)
    return record


def run_rollback(payload, run_id):
    sys.path.insert(0, "/opt/yrrp/project/scripts")
    from yrrp_ota.channel import Channel
    from yrrp_release.deployment_record import (
        build_deployment_record,
        publish_deployment_record,
        verify_record_artifacts,
    )
    globals().update(
        Channel=Channel,
        build_deployment_record=build_deployment_record,
        publish_deployment_record=publish_deployment_record,
        verify_record_artifacts=verify_record_artifacts,
    )
    channel = Channel.parse(payload["channel"])
    build_id = payload["build_id"]
    record_path = (
        SIGNED_ROOT / "deployments" / channel.device / channel.type / f"{build_id}.json"
    )
    write_status(run_id, "verifying-artifacts")
    if record_path.is_file() and not record_path.is_symlink():
        record = json.loads(record_path.read_text())
    else:
        legacy = payload.get("legacy_record")
        if legacy is None:
            raise SystemExit("deployment record is missing; provide validated receipt input")
        record = create_legacy_record(
            legacy, channel, build_id, payload["project_sha"]
        )
    paths = verify_record_artifacts(record, SIGNED_ROOT)
    command = [
        "/opt/yrrp/project/scripts/deploy-ota-release.sh",
        "--channel", channel.name,
        "--ota", str(paths["full_ota"]),
        "--target-files", str(paths["target_files"]),
        "--build-id", build_id,
    ]
    if "incremental" in paths:
        command.extend(["--incremental", str(paths["incremental"])])
    write_status(run_id, "deploying")
    environment = os.environ.copy()
    environment["YRRP_BUILD_LOCK_HELD"] = "1"
    completed = subprocess.run(
        command, capture_output=True, check=False, text=True, env=environment
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode
'''

REMOTE_ROLLBACK_WORKER_SCRIPT = (
    _REMOTE_COMMON
    + _REMOTE_JOB_BODY
    + r'''
payload = json.loads(sys.argv[1])
run_id = sys.argv[2]
ack_directory = Path(sys.argv[3])
lock = None
started = utc_now()
try:
    lock = acquire_nonblocking_lock()
    reject_active_release()
    synchronize_project(payload["project_sha"])
    atomic_json(ack_directory / "ack.json", {
        "ok": True,
        "run_id": run_id,
        "channel": payload["channel"],
        "build_id": payload["build_id"],
        "status": str(ROLLBACK_ROOT / run_id / "status.json"),
        "log": str(ROLLBACK_ROOT / run_id / "job.log"),
    }, exclusive=True)
    exit_code = run_rollback(payload, run_id)
    result_name = "passed" if exit_code == 0 else "failed"
    reason = None if exit_code == 0 else f"rollback-exit-{exit_code}"
    result = {
        "run_id": run_id,
        "phase": "complete" if exit_code == 0 else "failed",
        "result": result_name,
        "exit_code": exit_code,
        "channel": payload["channel"],
        "build_id": payload["build_id"],
        "started_at": started,
        "completed_at": utc_now(),
        "failure_reason": reason,
    }
    atomic_json(ROLLBACK_ROOT / run_id / "result.json", result, exclusive=True)
    write_status(run_id, result["phase"], result_name, exit_code, reason)
except BaseException as error:
    reason = str(error).strip().replace("\n", " ") or error.__class__.__name__
    write_status(run_id, "failed", "failed", 1, reason)
    result_path = ROLLBACK_ROOT / run_id / "result.json"
    if not result_path.exists():
        atomic_json(result_path, {
            "run_id": run_id,
            "phase": "failed",
            "result": "failed",
            "exit_code": 1,
            "channel": payload.get("channel"),
            "build_id": payload.get("build_id"),
            "started_at": started,
            "completed_at": utc_now(),
            "failure_reason": reason,
        }, exclusive=True)
    if not (ack_directory / "ack.json").exists():
        atomic_json(ack_directory / "ack.json", {"ok": False, "error": reason}, exclusive=True)
    raise
finally:
    if lock is not None:
        lock.close()
raise SystemExit(exit_code)
'''
)

REMOTE_ROLLBACK_SCRIPT = (
    _REMOTE_COMMON
    + "REMOTE_ROLLBACK_WORKER_SCRIPT = "
    + repr(REMOTE_ROLLBACK_WORKER_SCRIPT)
    + r'''
import secrets
import shutil
import time

payload = json.load(sys.stdin)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
run_id = f"rollback-{stamp}-{secrets.token_hex(4)}"
run_directory = Path("/opt/android/out/yrrp-rollbacks") / run_id
run_directory.mkdir(parents=True, mode=0o700)
log = run_directory / "job.log"
ack_directory = Path("/home/android") / f".yrrp-rollback-ack-{secrets.token_hex(16)}"
ack_directory.mkdir(mode=0o700)
run("sudo", "install", "-d", "-m", "0777", "-o", "root", "-g", "utmp", "/run/screen")
try:
    run(
        "screen", "-L", "-Logfile", str(log), "-dmS", run_id,
        "python3", "-c", REMOTE_ROLLBACK_WORKER_SCRIPT,
        json.dumps(payload, sort_keys=True), run_id, str(ack_directory),
    )
    acknowledgment_path = ack_directory / "ack.json"
    for _ in range(3000):
        if acknowledgment_path.is_file():
            acknowledgment = json.loads(acknowledgment_path.read_text())
            if not acknowledgment.get("ok"):
                raise SystemExit(
                    "detached rollback launch failed: "
                    + str(acknowledgment.get("error", "unknown error"))
                    + f"; screen log: {log}"
                )
            print(json.dumps({key: acknowledgment[key] for key in (
                "run_id", "channel", "build_id", "status", "log"
            )}, sort_keys=True))
            break
        screens = run("screen", "-list", check=False).stdout
        if f".{run_id}" not in screens:
            raise SystemExit(f"detached rollback exited without acknowledgment; screen log: {log}")
        time.sleep(0.1)
    else:
        raise SystemExit(f"timed out waiting for detached rollback; screen log: {log}")
finally:
    shutil.rmtree(ack_directory, ignore_errors=True)
'''
)

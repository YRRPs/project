from __future__ import annotations

import json

from .launcher import _REMOTE_COMMON
from .targets import TARGETS

CHECK_TARGET_SCRIPT = r'''
set -eo pipefail
cd /opt/android
device=$1
run_directory=$2
shift 2
source build/envsetup.sh >/dev/null
breakfast "${device}" >/dev/null
compile_ids=()
compile_targets=()
atest_ids=()
atest_targets=()
for target_id in "$@"; do
    case "${target_id}" in
        m:SystemUI-core) compile_ids+=("${target_id}"); compile_targets+=(SystemUI-core) ;;
        m:SystemUI) compile_ids+=("${target_id}"); compile_targets+=(SystemUI) ;;
        m:SettingsProvider) compile_ids+=("${target_id}"); compile_targets+=(SettingsProvider) ;;
        m:ThemePicker) compile_ids+=("${target_id}"); compile_targets+=(ThemePicker) ;;
        m:monet) compile_ids+=("${target_id}"); compile_targets+=(monet) ;;
        m:Settings) compile_ids+=("${target_id}"); compile_targets+=(Settings) ;;
        m:SettingsRoboTests) compile_ids+=("${target_id}"); compile_targets+=(SettingsRoboTests) ;;
        atest:monet_tests) atest_ids+=("${target_id}"); atest_targets+=(monet_tests) ;;
        atest:ThemeOverlayApplierTest) atest_ids+=("${target_id}"); atest_targets+=(ThemeOverlayApplierTest) ;;
        atest:ThemePickerRoboTests) atest_ids+=("${target_id}"); atest_targets+=(ThemePickerRoboTests) ;;
        atest:SystemUITests_presubmit) atest_ids+=("${target_id}"); atest_targets+=(SystemUITests_presubmit) ;;
        atest:SettingsProviderTest) atest_ids+=("${target_id}"); atest_targets+=(SettingsProviderTest) ;;
        atest:SystemUiRoboTests) atest_ids+=("${target_id}"); atest_targets+=(SystemUiRoboTests) ;;
        *) echo "unsupported target reached worker: ${target_id}" >&2; exit 64 ;;
    esac
done
results=${run_directory}/target-results.tsv
: > "${results}"
run_one() {
    local executor=$1 ids=$2 index=$3
    shift 3
    local output=${run_directory}/target-${index}.log
    local started completed exit_code
    started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    set +e
    "$@" >"${output}" 2>&1
    exit_code=$?
    set -e
    completed=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat "${output}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${executor}" "${ids}" "${exit_code}" "${started}" "${completed}" "${output}" \
        >> "${results}"
    return "${exit_code}"
}
index=0
if ((${#compile_targets[@]})); then
    compile_csv=$(IFS=,; echo "${compile_ids[*]}")
    run_one m "${compile_csv}" "${index}" m "${compile_targets[@]}" || exit $?
    index=$((index + 1))
fi
for suite_index in "${!atest_targets[@]}"; do
    run_one atest "${atest_ids[${suite_index}]}" "${index}" \
        atest "${atest_targets[${suite_index}]}" || exit $?
    index=$((index + 1))
done
'''

_TARGET_REGISTRY = {
    identifier: [target.executor, target.argument]
    for identifier, target in TARGETS.items()
}

_REMOTE_CHECK_BODY = r'''
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CHECK_ROOT = Path("/opt/android/out/yrrp-checks")
ATEST_PATTERN = re.compile(
    r"^[^:\n]+: Passed: (?P<passed>[0-9]+), Failed: (?P<failed>[0-9]+), "
    r"Ignored: (?P<ignored>[0-9]+)$", re.MULTILINE
)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_payload(payload):
    expected_keys = {"workspace", "project_sha", "repositories", "device", "targets"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise SystemExit("check payload keys are invalid")
    if payload["workspace"] not in ("isolated", "release"):
        raise SystemExit("check payload workspace is invalid")
    if payload["device"] not in ("salami", "xueying"):
        raise SystemExit("check payload device is invalid")
    if not isinstance(payload["project_sha"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", payload["project_sha"]
    ):
        raise SystemExit("check payload project SHA is invalid")
    repositories = payload["repositories"]
    if not isinstance(repositories, dict):
        raise SystemExit("check payload repositories are invalid")
    for relative, sha in repositories.items():
        path = Path(relative) if isinstance(relative, str) else None
        valid_path = (
            path is not None
            and not path.is_absolute()
            and relative == "/".join(path.parts)
            and path.parts
            and all(part not in (".", "..") for part in path.parts)
            and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part) for part in path.parts)
            and not path.parts[0].startswith("-")
        )
        if not valid_path or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise SystemExit("check payload repository is invalid")
    targets = payload["targets"]
    if not isinstance(targets, list) or not targets or len(set(targets)) != len(targets):
        raise SystemExit("check payload targets are invalid")
    if any(not isinstance(target, str) or target not in TARGET_REGISTRY for target in targets):
        raise SystemExit("check payload target is unsupported")


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
    atomic_json(CHECK_ROOT / run_id / "status.json", document)


def parse_target_results(run_directory, requested):
    results = []
    path = run_directory / "target-results.tsv"
    if not path.is_file():
        return results
    for raw_line in path.read_text().splitlines():
        executor, ids_csv, exit_text, started, completed, output_name = raw_line.split("\t")
        ids = ids_csv.split(",")
        expected = [TARGET_REGISTRY[target_id] for target_id in ids]
        if any(item[0] != executor for item in expected):
            raise SystemExit("target result executor mismatch")
        argv = [executor, *(item[1] for item in expected)]
        exit_code = int(exit_text)
        result = {
            "ids": ids,
            "executor": executor,
            "argv": argv,
            "result": "passed" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "started_at": started,
            "completed_at": completed,
        }
        if executor == "atest":
            output = Path(output_name).read_text(errors="replace")
            matches = list(ATEST_PATTERN.finditer(output))
            executed = 0
            if len(matches) == 1:
                executed = sum(
                    int(matches[0].group(name))
                    for name in ("passed", "failed", "ignored")
                )
            if exit_code == 0 and executed == 0:
                result["result"] = "failed"
                result["exit_code"] = 65
            result["executed_tests"] = executed
        results.append(result)
    flattened = [target_id for result in results for target_id in result["ids"]]
    if all(result["result"] == "passed" for result in results) and flattened != requested:
        raise SystemExit("successful target result set does not match request")
    return results


def synchronize_check_source(payload, initialize):
    if initialize and not (ANDROID / ".repo").is_dir():
        run(
            "repo", "init", "-u", "https://github.com/YRRPs/android.git",
            "-b", "lineage-23.2", cwd=ANDROID,
        )
        run("repo", "sync", "-c", "--no-tags", "--no-clone-bundle", cwd=ANDROID)
    validate_all_repositories_clean()
    for relative, expected_sha in sorted(payload["repositories"].items()):
        run(REPO, "sync", relative, cwd=ANDROID)
        root = ANDROID / relative
        remote_url = output("git", "remote", "get-url", "origin", cwd=root)
        if not remote_url:
            raise SystemExit(f"repository has no origin remote: {relative}")
        run("git", "fetch", "--no-tags", "origin", expected_sha, cwd=root)
        run("git", "cat-file", "-e", f"{expected_sha}^{{commit}}", cwd=root)
        run("git", "checkout", "--detach", expected_sha, cwd=root)
    return capture_source(payload["project_sha"], payload["repositories"])


def perform_check(payload, run_id, initialize=False):
    run_directory = CHECK_ROOT / run_id
    started = utc_now()
    write_status(run_id, "source-sync")
    source = synchronize_check_source(payload, initialize)
    write_status(run_id, "targets")
    command = [
        "bash", "-lc", CHECK_TARGET_SCRIPT, "yrrp-check",
        payload["device"], str(run_directory), *payload["targets"],
    ]
    completed_process = run(*command, cwd=ANDROID, check=False)
    if completed_process.stdout:
        sys.stdout.write(completed_process.stdout)
    if completed_process.stderr:
        sys.stderr.write(completed_process.stderr)
    target_results = parse_target_results(run_directory, payload["targets"])
    final_source = capture_source(payload["project_sha"], payload["repositories"])
    if final_source["manifest_sha256"] != source["manifest_sha256"]:
        raise SystemExit("source manifest changed during check")
    exit_code = completed_process.returncode
    all_passed = bool(target_results) and all(
        target["result"] == "passed" for target in target_results
    )
    if not all_passed and exit_code == 0:
        exit_code = 65
    result_name = "passed" if exit_code == 0 and all_passed else "failed"
    failure_reason = None if result_name == "passed" else f"check-exit-{exit_code}"
    document = {
        "schema_version": 1,
        "run_id": run_id,
        "workspace": payload["workspace"],
        "device": payload["device"],
        "source": {
            "project_sha": final_source["project_sha"],
            "repositories": final_source["repositories"],
            "manifest_sha256": final_source["manifest_sha256"],
        },
        "targets": target_results,
        "started_at": started,
        "completed_at": utc_now(),
        "log": str(run_directory / "job.log"),
        "result": result_name,
        "exit_code": exit_code,
        "failure_reason": failure_reason,
    }
    atomic_json(run_directory / "result.json", document, exclusive=True)
    write_status(run_id, "complete" if result_name == "passed" else "failed", result_name, exit_code, failure_reason)
    return exit_code
'''

REMOTE_CHECK_INNER_SCRIPT = (
    _REMOTE_COMMON
    + "TARGET_REGISTRY = "
    + repr(_TARGET_REGISTRY)
    + "\nCHECK_TARGET_SCRIPT = "
    + repr(CHECK_TARGET_SCRIPT)
    + _REMOTE_CHECK_BODY
    + r'''
payload = json.loads(sys.argv[1])
run_id = sys.argv[2]
validate_payload(payload)
validate_project(payload["project_sha"])
raise SystemExit(perform_check(payload, run_id, initialize=True))
'''
)

REMOTE_CHECK_WORKER_SCRIPT = (
    _REMOTE_COMMON
    + "TARGET_REGISTRY = "
    + repr(_TARGET_REGISTRY)
    + "\nCHECK_TARGET_SCRIPT = "
    + repr(CHECK_TARGET_SCRIPT)
    + "\nREMOTE_CHECK_INNER_SCRIPT = "
    + repr(REMOTE_CHECK_INNER_SCRIPT)
    + _REMOTE_CHECK_BODY
    + r'''
import shutil

payload = json.loads(sys.argv[1])
run_id = sys.argv[2]
ack_directory = Path(sys.argv[3])
lock = None
try:
    validate_payload(payload)
    lock = acquire_nonblocking_lock()
    reject_active_release()
    synchronize_project(payload["project_sha"])
    acknowledgment = {
        "ok": True,
        "run_id": run_id,
        "workspace": payload["workspace"],
        "status": str(CHECK_ROOT / run_id / "status.json"),
        "log": str(CHECK_ROOT / run_id / "job.log"),
    }
    atomic_json(ack_directory / "ack.json", acknowledgment, exclusive=True)
    if payload["workspace"] == "release":
        exit_code = perform_check(payload, run_id)
    else:
        inspection = output(
            "docker", "inspect", "--format", "{{.State.Running}} {{.Image}}",
            "lineageos-builder",
        ).split()
        if len(inspection) != 2 or inspection[0] != "true":
            raise SystemExit("lineageos-builder is not running")
        run(
            "docker", "run", "--rm", "--user", "0:0",
            "-v", "/mnt/fast/docker/android/check-workspace:/check-workspace:rw",
            "--entrypoint", "install", inspection[1],
            "-d", "-m", "0755", "-o", "950", "-g", "950", "/check-workspace",
        )
        host_results = (
            "/mnt/fast/docker/android/workspace/out/yrrp-checks/" + run_id
        )
        command = [
            "docker", "run", "--rm", "--user", "950:950",
            "-v", "/mnt/fast/docker/android/check-workspace:/opt/android:rw",
            "-v", "/mnt/fast/docker/android/ccache:/ccache:rw",
            "-v", "/mnt/fast/docker/android/project:/opt/yrrp/project:ro",
            "-v", host_results + ":" + str(CHECK_ROOT / run_id) + ":rw",
            "--entrypoint", "python3", inspection[1], "-c",
            REMOTE_CHECK_INNER_SCRIPT, json.dumps(payload, sort_keys=True), run_id,
        ]
        completed = run(*command, check=False)
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        exit_code = completed.returncode
except BaseException as error:
    reason = str(error).strip().replace("\n", " ") or error.__class__.__name__
    write_status(run_id, "failed", "failed", 1, reason)
    result_path = CHECK_ROOT / run_id / "result.json"
    if not result_path.exists():
        atomic_json(result_path, {
            "schema_version": 1,
            "run_id": run_id,
            "workspace": payload.get("workspace", "release"),
            "device": payload.get("device", "salami"),
            "source": {
                "project_sha": payload.get("project_sha", ""),
                "repositories": payload.get("repositories", {}),
                "manifest_sha256": "0" * 64,
            },
            "targets": [],
            "started_at": utc_now(),
            "completed_at": utc_now(),
            "log": str(CHECK_ROOT / run_id / "job.log"),
            "result": "failed",
            "exit_code": 1,
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

REMOTE_CHECK_SCRIPT = (
    _REMOTE_COMMON
    + "REMOTE_CHECK_WORKER_SCRIPT = "
    + repr(REMOTE_CHECK_WORKER_SCRIPT)
    + r'''
import secrets
import shutil
import time

payload = json.load(sys.stdin)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
run_id = f"check-{stamp}-{secrets.token_hex(4)}"
run_directory = Path("/opt/android/out/yrrp-checks") / run_id
run_directory.mkdir(parents=True, mode=0o700)
log = run_directory / "job.log"
ack_directory = Path("/home/android") / f".yrrp-check-ack-{secrets.token_hex(16)}"
ack_directory.mkdir(mode=0o700)
run(
    "sudo", "install", "-d", "-m", "0777", "-o", "root", "-g", "utmp", "/run/screen"
)
try:
    run(
        "screen", "-L", "-Logfile", str(log), "-dmS", run_id,
        "python3", "-c", REMOTE_CHECK_WORKER_SCRIPT,
        json.dumps(payload, sort_keys=True), run_id, str(ack_directory),
    )
    acknowledgment_path = ack_directory / "ack.json"
    for _ in range(3000):
        if acknowledgment_path.is_file():
            acknowledgment = json.loads(acknowledgment_path.read_text())
            if not acknowledgment.get("ok"):
                raise SystemExit(
                    "detached check launch failed: "
                    + str(acknowledgment.get("error", "unknown error"))
                    + f"; screen log: {log}"
                )
            print(json.dumps({
                "run_id": acknowledgment["run_id"],
                "workspace": acknowledgment["workspace"],
                "status": acknowledgment["status"],
                "log": acknowledgment["log"],
            }, sort_keys=True))
            break
        screens = run("screen", "-list", check=False).stdout
        if f".{run_id}" not in screens:
            raise SystemExit(f"detached check exited without acknowledgment; screen log: {log}")
        time.sleep(0.1)
    else:
        raise SystemExit(f"timed out waiting for detached check; screen log: {log}")
finally:
    shutil.rmtree(ack_directory, ignore_errors=True)
'''
)

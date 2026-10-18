from __future__ import annotations

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

ISOLATED_WORKSPACE_SETUP_SCRIPT = r'''
import os
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
uid = int(sys.argv[2])
gid = int(sys.argv[3])
os.chown(workspace, uid, gid)
workspace.chmod(0o755)
for name, mode in (("home", 0o700), ("out", 0o755), ("repos", 0o755)):
    path = workspace / name
    path.mkdir(mode=mode, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise SystemExit(f"isolated workspace {name} is not a directory")
    os.chown(path, uid, gid)
    path.chmod(mode)
'''

_REMOTE_CHECK_BODY = r'''
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

CHECK_ROOT = Path("/opt/android/out/yrrp-checks")
LOWER_ANDROID = Path("/opt/release-lower")
OVERLAY_ROOT = Path("/check-workspace/repos")
OVERLAY_STATE = Path("/check-workspace/active-repositories.json")
ATEST_PATTERN = re.compile(
    r"^(?:[^:\n]+:\s*)?Passed:\s*(?P<passed>[0-9]+),\s*"
    r"Failed:\s*(?P<failed>[0-9]+)"
    r"(?:,\s*Ignored:\s*(?P<ignored>[0-9]+))?$",
    re.MULTILINE,
)
ATEST_RESULT_LINE_PATTERN = re.compile(
    r"\b(?P<outcome>PASSED|FAILED)\s*$", re.MULTILINE
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


def ensure_isolated_result(payload, run_id, exit_code, check_root=None):
    if check_root is None:
        check_root = CHECK_ROOT
    result_path = check_root / run_id / "result.json"
    created = not result_path.is_file()
    if created:
        failure_code = exit_code if exit_code != 0 else 1
        reason = f"isolated-check-exit-{failure_code}-without-result"
        timestamp = utc_now()
        try:
            atomic_json(result_path, {
                "schema_version": 2,
                "run_id": run_id,
                "workspace": payload["workspace"],
                "device": payload["device"],
                "source": {
                    "project_sha": payload["project_sha"],
                    "repositories": payload["repositories"],
                    "manifest_sha256": "0" * 64,
                },
                "targets": [],
                "started_at": timestamp,
                "completed_at": timestamp,
                "log": str(check_root / run_id / "job.log"),
                "result": "failed",
                "exit_code": failure_code,
                "failure_reason": reason,
            }, exclusive=True)
        except FileExistsError:
            created = False
    result_document = json.loads(result_path.read_text())
    result_name = result_document["result"]
    terminal_status = {
        "run_id": run_id,
        "phase": "complete" if result_name == "passed" else "failed",
        "result": result_name,
        "exit_code": result_document["exit_code"],
    }
    failure_reason = result_document.get("failure_reason")
    if failure_reason is not None:
        terminal_status["failure_reason"] = failure_reason
    atomic_json(check_root / run_id / "status.json", terminal_status)
    return created


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
            summaries = list(ATEST_PATTERN.finditer(output))
            if summaries:
                passed = sum(int(match.group("passed")) for match in summaries)
                failed = sum(int(match.group("failed")) for match in summaries)
                ignored = sum(int(match.group("ignored") or 0) for match in summaries)
            else:
                outcomes = [
                    match.group("outcome")
                    for match in ATEST_RESULT_LINE_PATTERN.finditer(output)
                ]
                passed = outcomes.count("PASSED")
                failed = outcomes.count("FAILED")
                ignored = 0
            executed = passed + failed + ignored
            if exit_code == 0 and (executed == 0 or failed > 0):
                result["result"] = "failed"
                result["exit_code"] = 65
            result["executed_tests"] = executed
            result["passed_tests"] = passed
            result["failed_tests"] = failed
            result["ignored_tests"] = ignored
        results.append(result)
    flattened = [target_id for result in results for target_id in result["ids"]]
    if all(result["result"] == "passed" for result in results) and (
        len(flattened) != len(requested) or set(flattened) != set(requested)
    ):
        raise SystemExit("successful target result set does not match request")
    return results


def manifest_repository_remotes(manifest_xml):
    try:
        manifest = ET.fromstring(manifest_xml)
    except ET.ParseError as error:
        raise SystemExit("resolved repo manifest is invalid XML") from error
    defaults = manifest.findall("default")
    if len(defaults) > 1:
        raise SystemExit("resolved repo manifest has multiple defaults")
    default_remote = defaults[0].get("remote") if defaults else None
    remotes = {}
    for project in manifest.iter("project"):
        relative = project.get("path") or project.get("name")
        remote = project.get("remote") or default_remote
        if not relative or not remote:
            raise SystemExit("resolved repo manifest project has no path or remote")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", remote):
            raise SystemExit(f"resolved repo manifest has invalid remote: {remote}")
        if relative in remotes:
            raise SystemExit(f"resolved repo manifest has duplicate path: {relative}")
        remotes[relative] = remote
    return remotes


def fetch_repository_revision(root, remote_name, expected_sha):
    if remote_name not in output("git", "remote", cwd=root).splitlines():
        raise SystemExit(f"repository is missing manifest remote: {remote_name}")
    run("git", "fetch", "--no-tags", remote_name, expected_sha, cwd=root)
    run("git", "cat-file", "-e", f"{expected_sha}^{{commit}}", cwd=root)
    run("git", "checkout", "--detach", expected_sha, cwd=root)


def release_tree_fingerprint(android):
    repo = str(android / ".repo/repo/repo")
    manifests = android / ".repo/manifests"
    if output("git", "status", "--porcelain", cwd=manifests):
        raise SystemExit("release manifest checkout is dirty")
    local_directory = android / ".repo/local_manifests"
    local_hashes = {}
    if local_directory.is_dir():
        for path in sorted(local_directory.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix != ".xml":
                raise SystemExit(f"unsupported release local manifest entry: {path}")
            local_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    command = (
        'actual=$(git rev-parse HEAD) || exit 1; '
        'expected=$(git rev-parse "$REPO_LREV") || exit 1; '
        'dirty=$(git status --porcelain); '
        'if [ -n "$dirty" ] || [ "$actual" != "$expected" ]; then '
        'printf "%s\\nactual=%s\\nexpected=%s\\n%s\\n" '
        '"$REPO_PATH" "$actual" "$expected" "$dirty" >&2; exit 1; fi; '
        'printf "%s\\t%s\\n" "$REPO_PATH" "$actual"'
    )
    check = subprocess.run(
        [repo, "forall", "-e", "-c", command],
        cwd=android,
        capture_output=True,
        text=True,
    )
    if check.returncode:
        sys.stderr.write(check.stderr)
        raise SystemExit("release checkout is not clean at manifest revisions")
    manifest = output(repo, "manifest", "-r", cwd=android)
    repository_lines = sorted(line for line in check.stdout.splitlines() if line)
    return {
        "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "local_manifests": local_hashes,
        "repositories_sha256": hashlib.sha256(
            "\n".join(repository_lines).encode()
        ).hexdigest(),
    }


def overlay_path(overlay_root, relative):
    path = Path(relative)
    valid = (
        not path.is_absolute()
        and relative == "/".join(path.parts)
        and path.parts
        and all(part not in (".", "..") for part in path.parts)
        and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part) for part in path.parts)
    )
    if not valid:
        raise SystemExit(f"invalid overlay repository path: {relative}")
    return overlay_root.joinpath(*path.parts)


def load_overlay_state(active_path, overlay_root):
    if not active_path.is_file():
        return []
    try:
        active = json.loads(active_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("isolated overlay state is invalid") from error
    if not isinstance(active, list) or len(active) != len(set(active)):
        raise SystemExit("isolated overlay state is invalid")
    for relative in active:
        if not isinstance(relative, str):
            raise SystemExit("isolated overlay state is invalid")
        overlay_path(overlay_root, relative)
    return active


def write_overlay_state(active_path, repositories):
    atomic_json(active_path, sorted(repositories))


def remove_stale_overlays(previous, requested, overlay_root):
    for relative in sorted(set(previous) - set(requested)):
        stale = overlay_path(overlay_root, relative)
        if stale.is_symlink() or stale.is_file():
            stale.unlink()
        elif stale.is_dir():
            shutil.rmtree(stale)
        parent = stale.parent
        while parent != overlay_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def prepare_overlay_repository(source, overlay, remote_name, expected_sha):
    remote_url = output("git", "remote", "get-url", remote_name, cwd=source)
    if overlay.is_symlink():
        raise SystemExit(f"isolated overlay is a symlink: {overlay}")
    if overlay.exists() and not (overlay / ".git").is_dir():
        shutil.rmtree(overlay)
    if not overlay.exists():
        overlay.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--shared", "--no-checkout", str(source), str(overlay))
    else:
        run("git", "reset", "--hard", cwd=overlay)
        run("git", "clean", "-ffd", cwd=overlay)
    remotes = output("git", "remote", cwd=overlay).splitlines()
    if remote_name in remotes:
        run("git", "remote", "set-url", remote_name, remote_url, cwd=overlay)
    else:
        run("git", "remote", "add", remote_name, remote_url, cwd=overlay)
    fetch_repository_revision(overlay, remote_name, expected_sha)
    run("git", "clean", "-ffd", cwd=overlay)
    if output("git", "status", "--porcelain", cwd=overlay):
        raise SystemExit(f"isolated overlay checkout is dirty: {overlay}")


def prepare_overlay_repositories(
    repositories, manifest_remotes, lower_android, overlay_root, active_path
):
    previous = load_overlay_state(active_path, overlay_root)
    for relative in repositories:
        overlay_path(overlay_root, relative)
        if manifest_remotes.get(relative) is None:
            raise SystemExit(f"repository is missing from resolved manifest: {relative}")
    remove_stale_overlays(previous, repositories, overlay_root)
    # Track the planned overlays before creation so interrupted preparation is recoverable.
    write_overlay_state(active_path, repositories)
    for relative, expected_sha in sorted(repositories.items()):
        prepare_overlay_repository(
            lower_android / relative,
            overlay_path(overlay_root, relative),
            manifest_remotes[relative],
            expected_sha,
        )


def manifest_structure(element):
    # Ignore whitespace-only text and attribute order; keep child order and all other content.
    text = element.text if element.text and element.text.strip() else ""
    tail = element.tail if element.tail and element.tail.strip() else ""
    return (
        element.tag, tuple(sorted(element.attrib.items())), text, tail,
        tuple(manifest_structure(child) for child in element),
    )


def revision_locked_projects(manifest_xml):
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        manifest = ET.fromstring(manifest_xml, parser=parser)
    except ET.ParseError as error:
        raise SystemExit("resolved repo manifest is invalid XML") from error
    if manifest.tag != "manifest":
        raise SystemExit("resolved repo manifest has invalid root")
    projects = manifest.findall("project")
    if len(projects) != len(list(manifest.iter("project"))):
        raise SystemExit("nested manifest projects are unsupported for overlays")
    by_path = {}
    for project in projects:
        relative = project.get("path") or project.get("name")
        if not relative:
            raise SystemExit("resolved repo manifest project has no path")
        overlay_path(Path("/unused"), relative)
        if relative in by_path:
            raise SystemExit(f"resolved repo manifest has duplicate path: {relative}")
        if not re.fullmatch(r"[0-9a-f]{40}", project.get("revision", "")):
            raise SystemExit(f"manifest project is not revision locked: {relative}")
        by_path[relative] = project
    return manifest, by_path


def validate_overlay_manifest(lower_xml, overlay_xml, repositories):
    lower, lower_projects = revision_locked_projects(lower_xml)
    overlay, overlay_projects = revision_locked_projects(overlay_xml)
    for relative, expected_sha in repositories.items():
        if relative not in lower_projects or relative not in overlay_projects:
            raise SystemExit(f"requested project missing from manifest: {relative}")
        project = overlay_projects[relative]
        if project.get("revision") != expected_sha:
            raise SystemExit(f"overlay manifest revision mismatch: {relative}")
        project.set("revision", lower_projects[relative].get("revision"))
    if manifest_structure(lower) != manifest_structure(overlay):
        raise SystemExit("overlay manifest metadata differs from release lower layer")


def synchronize_check_source(payload, synchronize=True, include_manifest=False):
    validate_all_repositories_clean()
    if synchronize:
        manifest_remotes = manifest_repository_remotes(
            output(REPO, "manifest", "-r", cwd=ANDROID)
        )
        for relative, expected_sha in sorted(payload["repositories"].items()):
            run(REPO, "sync", relative, cwd=ANDROID)
            remote_name = manifest_remotes.get(relative)
            if remote_name is None:
                raise SystemExit(f"repository is missing from resolved manifest: {relative}")
            fetch_repository_revision(ANDROID / relative, remote_name, expected_sha)
    return capture_source(
        payload["project_sha"], payload["repositories"], include_manifest=include_manifest
    )


def perform_check(payload, run_id, synchronize=True, lower_android=None):
    run_directory = CHECK_ROOT / run_id
    started = utc_now()
    write_status(run_id, "source-sync")
    lower_before = (
        release_tree_fingerprint(lower_android) if lower_android is not None else None
    )
    source = synchronize_check_source(
        payload, synchronize, include_manifest=lower_before is not None
    )
    if lower_before is not None:
        lower_xml = output(
            str(lower_android / ".repo/repo/repo"), "manifest", "-r", cwd=lower_android
        )
        if hashlib.sha256(lower_xml.encode()).hexdigest() != lower_before["manifest_sha256"]:
            raise SystemExit("release lower manifest changed during capture")
        validate_overlay_manifest(lower_xml, source["manifest_xml"], payload["repositories"])
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
    if lower_before is not None:
        lower_after = release_tree_fingerprint(lower_android)
        if lower_after != lower_before:
            raise SystemExit("release lower layer changed during isolated check")
    exit_code = completed_process.returncode
    all_passed = bool(target_results) and all(
        target["result"] == "passed" for target in target_results
    )
    if not all_passed and exit_code == 0:
        exit_code = 65
    result_name = "passed" if exit_code == 0 and all_passed else "failed"
    failure_reason = None if result_name == "passed" else f"check-exit-{exit_code}"
    document = {
        "schema_version": 2,
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

ISOLATED_OVERLAY_PREP_SCRIPT = (
    _REMOTE_COMMON
    + "TARGET_REGISTRY = "
    + repr(_TARGET_REGISTRY)
    + "\nCHECK_TARGET_SCRIPT = "
    + repr(CHECK_TARGET_SCRIPT)
    + _REMOTE_CHECK_BODY
    + r'''
payload = json.loads(sys.argv[1])
validate_payload(payload)
validate_project(payload["project_sha"])
release_tree_fingerprint(LOWER_ANDROID)
lower_repo = str(LOWER_ANDROID / ".repo/repo/repo")
manifest_remotes = manifest_repository_remotes(
    output(lower_repo, "manifest", "-r", cwd=LOWER_ANDROID)
)
prepare_overlay_repositories(
    payload["repositories"],
    manifest_remotes,
    LOWER_ANDROID,
    OVERLAY_ROOT,
    OVERLAY_STATE,
)
'''
)

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
raise SystemExit(
    perform_check(payload, run_id, synchronize=False, lower_android=LOWER_ANDROID)
)
'''
)

REMOTE_CHECK_WORKER_SCRIPT = (
    _REMOTE_COMMON
    + "TARGET_REGISTRY = "
    + repr(_TARGET_REGISTRY)
    + "\nCHECK_TARGET_SCRIPT = "
    + repr(CHECK_TARGET_SCRIPT)
    + "\nISOLATED_WORKSPACE_SETUP_SCRIPT = "
    + repr(ISOLATED_WORKSPACE_SETUP_SCRIPT)
    + "\nISOLATED_OVERLAY_PREP_SCRIPT = "
    + repr(ISOLATED_OVERLAY_PREP_SCRIPT)
    + "\nREMOTE_CHECK_INNER_SCRIPT = "
    + repr(REMOTE_CHECK_INNER_SCRIPT)
    + _REMOTE_CHECK_BODY
    + r'''
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
        check_host = "/mnt/fast/docker/android/check-workspace"
        release_host = "/mnt/fast/docker/android/workspace"
        project_host = "/mnt/fast/docker/android/project"
        run(
            "docker", "run", "--rm", "--user", "0:0",
            "-v", check_host + ":/check-workspace:rw",
            "--entrypoint", "python3", inspection[1], "-c",
            ISOLATED_WORKSPACE_SETUP_SCRIPT, "/check-workspace", "950", "950",
        )
        run(
            "docker", "run", "--rm", "--user", "950:950",
            "--env", "HOME=/check-workspace/home",
            "--env", "REPO_TRACE=0",
            "--env", "GIT_OPTIONAL_LOCKS=0",
            "-v", release_host + ":/opt/release-lower:ro",
            "-v", check_host + ":/check-workspace:rw",
            "-v", project_host + ":/opt/yrrp/project:ro",
            "--entrypoint", "python3", inspection[1], "-c",
            ISOLATED_OVERLAY_PREP_SCRIPT, json.dumps(payload, sort_keys=True),
        )
        host_results = release_host + "/out/yrrp-checks/" + run_id
        command = [
            "docker", "run", "--rm", "--user", "950:950",
            "--env", "HOME=/home/android",
            "--env", "REPO_TRACE=0",
            "--env", "GIT_OPTIONAL_LOCKS=0",
            "-v", release_host + ":/opt/android:ro",
            "-v", release_host + ":/opt/release-lower:ro",
            "-v", check_host + "/out:/opt/android/out:rw",
            "-v", check_host + "/home:/home/android:rw",
            "-v", "/mnt/fast/docker/android/ccache:/ccache:rw",
            "-v", project_host + ":/opt/yrrp/project:ro",
            "-v", host_results + ":" + str(CHECK_ROOT / run_id) + ":rw",
        ]
        for relative in sorted(payload["repositories"]):
            command.extend([
                "-v",
                check_host + "/repos/" + relative + ":/opt/android/" + relative + ":rw",
            ])
        command.extend([
            "--entrypoint", "python3", inspection[1], "-c",
            REMOTE_CHECK_INNER_SCRIPT, json.dumps(payload, sort_keys=True), run_id,
        ])
        completed = run(*command, check=False)
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        missing_result = ensure_isolated_result(
            payload, run_id, completed.returncode
        )
        exit_code = completed.returncode
        if missing_result and exit_code == 0:
            exit_code = 1
except BaseException as error:
    reason = str(error).strip().replace("\n", " ") or error.__class__.__name__
    write_status(run_id, "failed", "failed", 1, reason)
    result_path = CHECK_ROOT / run_id / "result.json"
    if not result_path.exists():
        atomic_json(result_path, {
            "schema_version": 2,
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
        "env", "REPO_TRACE=0", "GIT_OPTIONAL_LOCKS=0",
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

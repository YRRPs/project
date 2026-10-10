from __future__ import annotations

import hashlib
import html
import ipaddress
import os
import re
import secrets
import stat
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yrrp_ota.channel import BUILD_ID_PATTERN, Channel

from .launcher import APPROVAL, PROJECT_ROOT, validate_repo_path, validate_sha, validate_sha256
from .receipt_channel import (
    INCREMENTAL_SUFFIX,
    expected_routes,
    incremental_source,
    receipt_stem,
    release_channel,
    require_carried_routes_unchanged,
    required_artifact_names,
    validate_carried_channels,
)

MARKER_PATTERN = re.compile(r"\b(?:tbd|todo)\b", re.IGNORECASE)
TEMPLATE_PATTERN = re.compile(
    r"(?:\{\{[^{}]+\}\}|\$\{[^{}]+\}|__[^_]+__|<[^<>]*(?:placeholder|insert|fill)[^<>]*>)",
    re.IGNORECASE,
)
UNKNOWN_PATTERN = re.compile(
    r"\b(?:unknown|not known|pending|n/a|not[- ]applicable|placeholder)\b",
    re.IGNORECASE,
)
HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
PROOF_VERDICTS = frozenset({"PROVEN", "FAILED", "UNPROVEN"})
TOP_LEVEL_SECTIONS = (
    "release_identity",
    "selected_changes",
    "source",
    "build_and_signing",
    "artifacts",
    "deployment_public_checks",
    "proof_outcomes",
    "device_restoration",
    "known_gaps_observations",
)
PUBLIC_CHECK_FIELDS = {
    "healthz": ("name", "url", "status", "evidence"),
    "updates metadata": ("name", "url", "status", "evidence", "build_id"),
    "install listing": ("name", "url", "status", "evidence", "build_id"),
    "full OTA range": ("name", "url", "status", "evidence", "artifact_name"),
    "incremental OTA range": ("name", "url", "status", "evidence", "artifact_name"),
    "stale fallback": (
        "name",
        "url",
        "status",
        "evidence",
        "redirected",
        "artifact_name",
        "build_id",
    ),
}
PROOF_FIELDS = (
    "feature",
    "claim",
    "trigger_and_setup",
    "observable_evidence",
    "expected_outcome",
    "observed_outcome",
    "verdict",
    "limitations",
    "post_release_device_check",
    "restoration",
)

def render_receipt(document: dict[str, Any]) -> str:
    validated = _validate_document(document)
    sections = [
        _release_identity(validated),
        _selected_changes(validated),
        _source(validated),
        _build_and_signing(validated),
        _artifacts(validated),
        _deployment(validated),
        _proof_outcomes(validated),
        _device_restoration(validated),
        _known_gaps(validated),
    ]
    return "# YRRP release receipt\n\n" + "\n\n".join(sections) + "\n"

def write_receipt(document: dict[str, Any]) -> Path:
    return _write_receipt(document, PROJECT_ROOT)

def _write_receipt(document: dict[str, Any], root: Path) -> Path:
    rendered = render_receipt(document)
    identity = document["release_identity"]
    stem = receipt_stem(release_channel(identity), identity["build_id"])
    destination = root / ".claude" / "releases" / f"{stem}.md"
    root_fd, claude_fd, releases_fd = _open_release_directory(root)
    try:
        _publish_receipt(
            root_fd,
            claude_fd,
            releases_fd,
            stem,
            rendered.encode("utf-8"),
            destination,
        )
    finally:
        os.close(releases_fd)
        os.close(claude_fd)
        os.close(root_fd)
    return destination

def _open_release_directory(root: Path) -> tuple[int, int, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, flags)
    claude_fd = -1
    releases_fd = -1
    try:
        claude_fd = _open_or_create_directory(root_fd, ".claude", 0o700)
        releases_fd = _open_or_create_directory(claude_fd, "releases", 0o700)
        os.fchmod(releases_fd, 0o700)
        if stat.S_IMODE(os.fstat(releases_fd).st_mode) != 0o700:
            raise PermissionError("receipt directory mode is not 0700")
        return root_fd, claude_fd, releases_fd
    except BaseException:
        if releases_fd >= 0:
            os.close(releases_fd)
        if claude_fd >= 0:
            os.close(claude_fd)
        os.close(root_fd)
        raise

def _open_or_create_directory(parent_fd: int, name: str, mode: int) -> int:
    created = False
    try:
        os.mkdir(name, mode=mode, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )
    try:
        if created:
            os.fsync(parent_fd)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor

def _publish_receipt(
    root_fd: int,
    claude_fd: int,
    releases_fd: int,
    stem: str,
    content: bytes,
    destination: Path,
) -> None:
    temporary_name, descriptor = _create_temporary(releases_fd, stem)
    published = False
    temporary_exists = True
    preserve_final_on_error = False
    try:
        os.fchmod(descriptor, 0o600)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise PermissionError("receipt mode is not 0600")
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _require_held_directory(root_fd, claude_fd, releases_fd)
        os.link(
            temporary_name,
            f"{stem}.md",
            src_dir_fd=releases_fd,
            dst_dir_fd=releases_fd,
            follow_symlinks=False,
        )
        published = True
        _require_held_directory(root_fd, claude_fd, releases_fd)
        os.unlink(temporary_name, dir_fd=releases_fd)
        temporary_exists = False
        try:
            os.fsync(releases_fd)
        except BaseException:
            preserve_final_on_error = True
            raise
    except FileExistsError as error:
        raise FileExistsError(f"receipt already exists: {destination}") from error
    except BaseException as error:
        if published and not preserve_final_on_error:
            try:
                os.unlink(f"{stem}.md", dir_fd=releases_fd)
            except OSError as cleanup_error:
                error.add_note(f"could not remove failed final receipt: {cleanup_error}")
        raise
    finally:
        _cleanup_temporary(releases_fd, temporary_name, descriptor, temporary_exists)

def _cleanup_temporary(
    releases_fd: int, name: str, descriptor: int, exists: bool
) -> None:
    if descriptor >= 0:
        os.close(descriptor)
    if not exists:
        return
    active_error = sys.exc_info()[1]
    try:
        os.unlink(name, dir_fd=releases_fd)
        os.fsync(releases_fd)
    except OSError as cleanup_error:
        if active_error is None:
            raise
        active_error.add_note(f"receipt temporary cleanup failed: {cleanup_error}")

def _create_temporary(releases_fd: int, stem: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f".{stem}.{secrets.token_hex(8)}.tmp"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=releases_fd)
        except FileExistsError:
            continue
    raise FileExistsError("could not allocate unique receipt temporary file")

def _require_held_directory(
    root_fd: int, claude_fd: int, releases_fd: int
) -> None:
    _require_linked_directory(root_fd, ".claude", claude_fd)
    _require_linked_directory(claude_fd, "releases", releases_fd)

def _require_linked_directory(parent_fd: int, name: str, held_fd: int) -> None:
    linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    held = os.fstat(held_fd)
    if not stat.S_ISDIR(linked.st_mode) or (linked.st_dev, linked.st_ino) != (
        held.st_dev,
        held.st_ino,
    ):
        raise OSError("receipt directory changed during publication")

def _validate_document(document: Any) -> dict[str, Any]:
    result = _mapping(document, "receipt")
    _exact_keys(
        result,
        "receipt",
        TOP_LEVEL_SECTIONS,
        optional=("accessibility_exclusion",),
    )
    _validate_identity(result)
    _validate_changes(result)
    _validate_source(result)
    _validate_build_and_signing(result)
    _validate_artifacts(result)
    _validate_deployment(result)
    _validate_proofs(result)
    _validate_restoration(result)
    _validate_known_gaps(result)
    _validate_accessibility_exclusion(result)
    _validate_success_gate(result)
    return result

def _validate_identity(document: dict[str, Any]) -> None:
    identity = _mapping(document["release_identity"], "release_identity")
    _exact_keys(
        identity,
        "release_identity",
        ("build_id", "completion", "overall_result", "approval"),
        optional=("channel",),
    )
    release_channel(identity)
    build_id = _text(identity, "build_id", "release_identity")
    if not BUILD_ID_PATTERN.fullmatch(build_id):
        raise ValueError("build ID must match YYYYMMDD-HHMMSS")
    try:
        built_at = datetime.strptime(build_id, "%Y%m%d-%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ValueError("build ID must contain a real UTC-like date and time") from error
    completion = _text(identity, "completion", "release_identity")
    try:
        completed_at = datetime.fromisoformat(completion.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("completion must be an ISO-8601 timestamp") from error
    if completed_at.tzinfo is None:
        raise ValueError("completion must include a timezone")
    completed_utc = completed_at.astimezone(timezone.utc)
    if completed_utc < built_at:
        raise ValueError("completion must not precede the build ID timestamp")
    if completed_utc - built_at > timedelta(days=30):
        raise ValueError("completion is implausibly far after the build ID timestamp")
    result = _text(identity, "overall_result", "release_identity")
    if result not in {"SUCCESS", "FAILED"}:
        raise ValueError("overall_result must be SUCCESS or FAILED")
    approval = _text(identity, "approval", "release_identity")
    if approval != APPROVAL:
        raise ValueError(f"approval must be exactly {APPROVAL!r}")

def _validate_changes(document: dict[str, Any]) -> None:
    changes = _nonempty_list(document["selected_changes"], "selected_changes")
    urls: set[str] = set()
    for index, raw in enumerate(changes):
        label = f"selected_changes[{index}]"
        change = _mapping(raw, label)
        _exact_keys(
            change,
            label,
            (
                "pr_url",
                "repository",
                "branch",
                "base_sha",
                "tested_head_sha",
                "tested_patch_id",
                "merged_sha",
                "merged_patch_id",
                "ancestry",
                "local_test_evidence",
                "acceptance_criteria",
            ),
        )
        url = _https_url(_text(change, "pr_url", label), f"{label}.pr_url")
        repository = _text(change, "repository", label)
        if repository != "project":
            validate_repo_path(repository)
        _text(change, "branch", label)
        validate_sha(_text(change, "base_sha", label), f"{label}.base_sha")
        validate_sha(
            _text(change, "tested_head_sha", label), f"{label}.tested_head_sha"
        )
        validate_sha(
            _text(change, "tested_patch_id", label), f"{label}.tested_patch_id"
        )
        validate_sha(_text(change, "merged_sha", label), f"{label}.merged_sha")
        validate_sha(
            _text(change, "merged_patch_id", label), f"{label}.merged_patch_id"
        )
        _validate_ancestry(change["ancestry"], label)
        _text(change, "local_test_evidence", label)
        criteria = _nonempty_list(change.get("acceptance_criteria"), f"{label}.acceptance_criteria")
        validated_criteria = [
            _list_text(criteria, criterion_index, f"{label}.acceptance_criteria")
            for criterion_index in range(len(criteria))
        ]
        if len(set(validated_criteria)) != len(validated_criteria):
            raise ValueError(f"duplicate acceptance criteria in {label}")
        if url in urls:
            raise ValueError(f"duplicate PR URL: {url}")
        urls.add(url)

def _validate_ancestry(value: Any, change_label: str) -> None:
    label = f"{change_label}.ancestry"
    ancestry = _mapping(value, label)
    _exact_keys(
        ancestry,
        label,
        ("source_sha", "ancestor_sha", "descendant_sha", "check", "exit_code"),
    )
    for field in ("source_sha", "ancestor_sha", "descendant_sha"):
        validate_sha(_text(ancestry, field, label), f"{label}.{field}")
    _text(ancestry, "check", label)
    exit_code = ancestry["exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise TypeError(f"{label}.exit_code must be an integer")

def _validate_source(document: dict[str, Any]) -> None:
    source = _mapping(document["source"], "source")
    _exact_keys(
        source,
        "source",
        ("project_sha", "repositories", "manifest_sha256", "manifest_xml", "local_manifests", "builder_log"),
    )
    validate_sha(_text(source, "project_sha", "source"), "source.project_sha")
    manifest_sha256 = validate_sha256(
        _text(source, "manifest_sha256", "source"), "source.manifest_sha256"
    )
    manifest_xml = source["manifest_xml"]
    if not isinstance(manifest_xml, str) or not manifest_xml.strip():
        raise TypeError("source.manifest_xml must be a non-empty string")
    for character in manifest_xml:
        category = unicodedata.category(character)
        prohibited_control = category == "Cc" and character not in "\n\r\t"
        if category in {"Cf", "Cs"} or prohibited_control:
            raise ValueError("source.manifest_xml contains a prohibited Unicode control")
    if hashlib.sha256(manifest_xml.encode()).hexdigest() != manifest_sha256:
        raise ValueError("source manifest XML does not match manifest SHA-256")
    local_manifests = _mapping(source["local_manifests"], "source.local_manifests")
    for name, digest in local_manifests.items():
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".xml"):
            raise ValueError(f"invalid local manifest name: {name!r}")
        try:
            validate_sha256(digest, f"source.local_manifests[{name!r}]")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid local manifest digest: {name}") from error
    _text(source, "builder_log", "source")
    repositories = _list(source["repositories"], "source.repositories")
    seen: set[str] = set()
    for index, raw in enumerate(repositories):
        label = f"source.repositories[{index}]"
        repository = _mapping(raw, label)
        _exact_keys(repository, label, ("repository", "sha"))
        name = validate_repo_path(_text(repository, "repository", label))
        validate_sha(_text(repository, "sha", label), f"{label}.sha")
        if name in seen:
            raise ValueError(f"duplicate repository: {name}")
        seen.add(name)

def _validate_build_and_signing(document: dict[str, Any]) -> None:
    section = _mapping(document["build_and_signing"], "build_and_signing")
    _exact_keys(
        section,
        "build_and_signing",
        (
            "build_result",
            "signing_verified",
            "evidence",
            "checksum_verification_output",
        ),
    )
    result = _text(section, "build_result", "build_and_signing")
    if result not in {"SUCCESS", "FAILED"}:
        raise ValueError("build_result must be SUCCESS or FAILED")
    _boolean(section, "signing_verified", "build_and_signing")
    _text(section, "evidence", "build_and_signing")
    output = _list(
        section["checksum_verification_output"],
        "build_and_signing.checksum_verification_output",
    )
    for index, line in enumerate(output):
        _validated_text(line, f"build_and_signing.checksum_verification_output[{index}]")

def _validate_artifacts(document: dict[str, Any]) -> None:
    artifacts = _list(document["artifacts"], "artifacts")
    names: set[str] = set()
    for index, raw in enumerate(artifacts):
        label = f"artifacts[{index}]"
        artifact = _mapping(raw, label)
        _exact_keys(
            artifact,
            label,
            ("name", "size", "sha256", "checksum_verified"),
        )
        name = _text(artifact, "name", label)
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"{label}.name must be a plain filename")
        size = artifact.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise TypeError(f"{label}.size must be a non-negative integer")
        validate_sha256(_text(artifact, "sha256", label), f"{label}.sha256")
        _boolean(artifact, "checksum_verified", label)
        if name in names:
            raise ValueError(f"duplicate artifact name: {name}")
        names.add(name)

def _validate_deployment(document: dict[str, Any]) -> None:
    section = _mapping(
        document["deployment_public_checks"], "deployment_public_checks"
    )
    _exact_keys(
        section,
        "deployment_public_checks",
        ("deployment_verified", "installed_build_id", "checks"),
        optional=("container", "carried_channels"),
    )
    _boolean(section, "deployment_verified", "deployment_public_checks")
    installed = section.get("installed_build_id")
    if not isinstance(installed, str):
        raise TypeError("deployment_public_checks.installed_build_id must be a string")
    if installed and not BUILD_ID_PATTERN.fullmatch(installed):
        raise ValueError("installed build ID must match YYYYMMDD-HHMMSS")
    if "container" in section:
        _validate_container_schema(section["container"])
    if "carried_channels" in section:
        released = release_channel(document["release_identity"])
        validate_carried_channels(section["carried_channels"], released)
    checks = _list(section["checks"], "deployment_public_checks.checks")
    names: set[str] = set()
    for index, raw in enumerate(checks):
        name = _validate_public_check(raw, index)
        if name in names:
            raise ValueError(f"duplicate deployment check name: {name}")
        names.add(name)

def _validate_container_schema(value: Any) -> None:
    label = "deployment_public_checks.container"
    container = _mapping(value, label)
    _exact_keys(container, label, ("healthy", "build_id_label", "image_label"))
    _boolean(container, "healthy", label)
    _text(container, "build_id_label", label)
    _text(container, "image_label", label)

def _validate_public_check(value: Any, index: int) -> str:
    label = f"deployment_public_checks.checks[{index}]"
    check = _mapping(value, label)
    name = _text(check, "name", label)
    fields = PUBLIC_CHECK_FIELDS.get(name)
    if fields is None:
        raise ValueError(f"unknown public check name: {name}")
    _exact_keys(check, label, fields)
    _https_url(_text(check, "url", label), f"{label}.url")
    status = check["status"]
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise TypeError(f"{label}.status must be an HTTP status integer")
    _text(check, "evidence", label)
    if "build_id" in check:
        check_build = _text(check, "build_id", label)
        if not BUILD_ID_PATTERN.fullmatch(check_build):
            raise ValueError(f"{label}.build_id must match YYYYMMDD-HHMMSS")
    if "redirected" in check:
        _boolean(check, "redirected", label)
    if "artifact_name" in check:
        _text(check, "artifact_name", label)
    return name

def _validate_proofs(document: dict[str, Any]) -> None:
    proofs = _nonempty_list(document["proof_outcomes"], "proof_outcomes")
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(proofs):
        label = f"proof_outcomes[{index}]"
        proof = _mapping(raw, label)
        _exact_keys(proof, label, PROOF_FIELDS)
        for field in PROOF_FIELDS:
            _text(proof, field, label)
        if proof["verdict"] not in PROOF_VERDICTS:
            raise ValueError(f"{label}.verdict must be PROVEN, FAILED, or UNPROVEN")
        identity = (proof["feature"], proof["claim"])
        if identity in identities:
            raise ValueError(f"duplicate proof identity: {identity[0]} / {identity[1]}")
        identities.add(identity)

def _validate_restoration(document: dict[str, Any]) -> None:
    section = _mapping(document["device_restoration"], "device_restoration")
    _exact_keys(section, "device_restoration", ("completed", "evidence"))
    _boolean(section, "completed", "device_restoration")
    _text(section, "evidence", "device_restoration")

def _validate_known_gaps(document: dict[str, Any]) -> None:
    gaps = _list(document["known_gaps_observations"], "known_gaps_observations")
    validated = [
        _validated_text(
            value,
            f"known_gaps_observations[{index}]",
            allow_unknown=True,
        )
        for index, value in enumerate(gaps)
    ]
    if len(set(validated)) != len(validated):
        raise ValueError("duplicate known gap or observation")

def _validate_accessibility_exclusion(document: dict[str, Any]) -> None:
    if "accessibility_exclusion" in document:
        _text(document, "accessibility_exclusion", "receipt")

def _validate_success_gate(document: dict[str, Any]) -> None:
    if document["release_identity"]["overall_result"] != "SUCCESS":
        if not document["known_gaps_observations"]:
            raise ValueError("FAILED receipt requires a gap or reason")
        return
    failures: list[str] = []
    if document["build_and_signing"]["build_result"] != "SUCCESS":
        failures.append("build")
    if not document["build_and_signing"]["signing_verified"]:
        failures.append("signing")
    deployment = document["deployment_public_checks"]
    if not deployment["deployment_verified"]:
        failures.append("deployment")
    expected = document["release_identity"]["build_id"]
    if deployment["installed_build_id"] != expected:
        failures.append("installed build identity")
    if any(proof["verdict"] != "PROVEN" for proof in document["proof_outcomes"]):
        failures.append("proof")
    if not document["device_restoration"]["completed"]:
        failures.append("device restoration")
    if failures:
        raise ValueError("SUCCESS receipt has incomplete verification: " + ", ".join(failures))
    _validate_success_sources(document)
    incremental = _validate_success_artifacts(document)
    _validate_success_public_checks(document, incremental)
    require_carried_routes_unchanged(deployment.get("carried_channels", []))

def _validate_success_sources(document: dict[str, Any]) -> None:
    source = document["source"]
    repositories = {
        item["repository"]: item["sha"] for item in source["repositories"]
    }
    selected_android: set[str] = set()
    for change in document["selected_changes"]:
        repository = change["repository"]
        source_sha = source["project_sha"] if repository == "project" else repositories.get(repository)
        if source_sha is None:
            raise ValueError(f"selected repository is missing from frozen source: {repository}")
        ancestry = change["ancestry"]
        correlated = (
            ancestry["source_sha"] == source_sha
            and ancestry["ancestor_sha"] == change["merged_sha"]
            and ancestry["descendant_sha"] == source_sha
            and ancestry["check"] == "git merge-base --is-ancestor"
            and ancestry["exit_code"] == 0
        )
        if not correlated:
            raise ValueError(f"selected change ancestry evidence is invalid: {change['pr_url']}")
        if change["tested_patch_id"] != change["merged_patch_id"]:
            raise ValueError(
                f"selected change tested patch does not match merged patch: {change['pr_url']}"
            )
        if repository != "project":
            selected_android.add(repository)
    unrelated = set(repositories) - selected_android
    if unrelated:
        raise ValueError("source contains unrelated repositories: " + ", ".join(sorted(unrelated)))

def _validate_success_artifacts(document: dict[str, Any]) -> str | None:
    """Validate the signed artifact set; return the incremental artifact name, if any."""
    build_id = document["release_identity"]["build_id"]
    channel = release_channel(document["release_identity"])
    artifacts = {artifact["name"]: artifact for artifact in document["artifacts"]}
    if not required_artifact_names(channel, build_id).issubset(artifacts):
        raise ValueError("SUCCESS receipt is missing a required signed artifact")
    if any(
        artifact["size"] <= 0 or not artifact["checksum_verified"]
        for artifact in document["artifacts"]
    ):
        raise ValueError("SUCCESS artifact sizes and checksum verification must be complete")
    incrementals = [name for name in artifacts if INCREMENTAL_SUFFIX in name]
    if len(incrementals) > 1:
        raise ValueError("SUCCESS receipt may list at most one incremental artifact")
    if incrementals:
        _validate_incremental_name(incrementals[0], build_id, channel)
    _validate_checksum_output(document, channel.checksums_name(build_id))
    return incrementals[0] if incrementals else None

def _validate_checksum_output(document: dict[str, Any], manifest_name: str) -> None:
    expected = {
        f"{artifact['name']}: OK"
        for artifact in document["artifacts"]
        if artifact["name"] != manifest_name
    }
    output = document["build_and_signing"]["checksum_verification_output"]
    if len(output) != len(set(output)) or set(output) != expected:
        raise ValueError("checksum verification output does not exactly match artifacts")

def _validate_incremental_name(name: str, target_build_id: str, channel: Channel) -> None:
    source = incremental_source(name, target_build_id, channel)
    try:
        source_time = datetime.strptime(source, "%Y%m%d-%H%M%S")
        target_time = datetime.strptime(target_build_id, "%Y%m%d-%H%M%S")
    except ValueError as error:
        raise ValueError("incremental artifact source build ID is invalid") from error
    if source_time >= target_time:
        raise ValueError("incremental artifact source build must be strictly older than target")

def _validate_success_public_checks(
    document: dict[str, Any], incremental: str | None
) -> None:
    deployment = document["deployment_public_checks"]
    checks = {check["name"]: check for check in deployment["checks"]}
    statuses, paths = _expected_public_checks(document, incremental)
    if set(checks) != set(statuses):
        raise ValueError("public check names do not exactly match the required set")
    for name, status_code in statuses.items():
        if checks[name]["status"] != status_code:
            raise ValueError(f"required public check is missing or failed: {name}")
    build_id = document["release_identity"]["build_id"]
    _validate_success_container(deployment.get("container"), build_id)
    for name, path in paths.items():
        parsed = urlsplit(checks[name]["url"])
        if parsed.netloc != "ota.yimura.dev" or parsed.path != path or parsed.query:
            raise ValueError(f"public check URL does not match required route: {name}")
    full_name = release_channel(document["release_identity"]).full_ota_name(build_id)
    if checks["updates metadata"].get("build_id") != build_id:
        raise ValueError("updates metadata public check does not identify the build")
    if checks["install listing"].get("build_id") != build_id:
        raise ValueError("install listing public check does not identify the build")
    for name in ("full OTA range", "incremental OTA range"):
        if name in checks:
            basename = paths[name].rsplit("/", 1)[-1]
            if checks[name].get("artifact_name") != basename:
                raise ValueError(f"{name} public check does not identify its artifact")
    stale = checks["stale fallback"]
    stale_matches = (
        stale.get("redirected") is False
        and stale.get("artifact_name") == full_name
        and stale.get("build_id") == build_id
    )
    if not stale_matches:
        raise ValueError("stale fallback must directly return the current full artifact")

def _expected_public_checks(
    document: dict[str, Any], incremental: str | None
) -> tuple[dict[str, int], dict[str, str]]:
    identity = document["release_identity"]
    statuses = {
        "healthz": 200,
        "updates metadata": 200,
        "stale fallback": 200,
        "install listing": 200,
        "full OTA range": 206,
    }
    if incremental is not None:
        statuses["incremental OTA range"] = 206
    paths = expected_routes(release_channel(identity), identity["build_id"], incremental)
    return statuses, paths

def _validate_success_container(value: Any, build_id: str) -> None:
    if not value or (
        not value["healthy"]
        or value["build_id_label"] != build_id
        or value["image_label"] != "true"
    ):
        raise ValueError("deployment container health or labels do not match the release")

def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value

def _exact_keys(
    mapping: dict[str, Any],
    label: str,
    required: tuple[str, ...],
    *,
    optional: tuple[str, ...] = (),
) -> None:
    missing = set(required) - set(mapping)
    if missing:
        raise ValueError(f"missing required field in {label}: {sorted(missing)[0]}")
    unknown = set(mapping) - set(required) - set(optional)
    if unknown:
        raise ValueError(f"unknown field in {label}: {sorted(unknown)[0]}")

def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a JSON array")
    return value

def _nonempty_list(value: Any, label: str) -> list[Any]:
    result = _list(value, label)
    if not result:
        raise TypeError(f"{label} must be a non-empty JSON array")
    return result

def _text(mapping: dict[str, Any], key: str, label: str) -> str:
    if key not in mapping:
        raise ValueError(f"missing required field: {label}.{key}")
    return _validated_text(mapping[key], f"{label}.{key}")

def _validated_text(value: Any, label: str, *, allow_unknown: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    for character in value:
        category = unicodedata.category(character)
        prohibited_control = category == "Cc" and character not in "\n\r\t"
        if category in {"Cf", "Cs"} or prohibited_control:
            raise ValueError(f"{label} contains a prohibited Unicode control")
    stripped = value.strip()
    has_placeholder = MARKER_PATTERN.search(stripped) or TEMPLATE_PATTERN.search(stripped)
    word_placeholder = UNKNOWN_PATTERN.search(stripped)
    standalone_placeholder = word_placeholder and word_placeholder.group(0) == stripped
    if has_placeholder or standalone_placeholder or (not allow_unknown and word_placeholder):
        raise ValueError(f"placeholder in required field: {label}")
    return value

def _list_text(values: list[Any], index: int, label: str) -> str:
    return _validated_text(values[index], f"{label}[{index}]")

def _boolean(mapping: dict[str, Any], key: str, label: str) -> bool:
    if key not in mapping or not isinstance(mapping[key], bool):
        raise TypeError(f"{label}.{key} must be a boolean")
    return mapping[key]

def _https_url(value: str, label: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{label} URL contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError(f"{label} has a malformed URL authority") from error
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or authority.endswith(":")
        or port == 0
        or not _valid_hostname(hostname)
    ):
        raise ValueError(f"{label} must be a valid HTTPS URL without credentials or fragment")
    return value

def _valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_name = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_name) > 253:
        return False
    labels = ascii_name.rstrip(".").split(".")
    return bool(labels) and all(HOST_LABEL_PATTERN.fullmatch(label) for label in labels)

def _md(value: Any) -> str:
    escaped = html.escape(str(value), quote=False)
    flattened = " ".join(escaped.split())
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        flattened = flattened.replace(character, "\\" + character)
    return flattened

def _release_identity(document: dict[str, Any]) -> str:
    value = document["release_identity"]
    return _section(
        "Release identity",
        (
            ("Build ID", value["build_id"]),
            ("Channel", release_channel(value).name),
            ("Completion", value["completion"]),
            ("Overall result", value["overall_result"]),
            ("Exact approval", value["approval"]),
        ),
    )

def _selected_changes(document: dict[str, Any]) -> str:
    lines = ["## Selected changes"]
    changes = sorted(document["selected_changes"], key=lambda item: (item["repository"], item["pr_url"]))
    for change in changes:
        lines.extend(
            [
                f"### {_md(change['repository'])}",
                f"- PR URL: {_md(change['pr_url'])}",
                f"- Branch: {_md(change['branch'])}",
                f"- Base SHA: `{_md(change['base_sha'])}`",
                f"- Tested head SHA: `{_md(change['tested_head_sha'])}`",
                f"- Tested patch ID: `{_md(change['tested_patch_id'])}`",
                f"- Merged SHA: `{_md(change['merged_sha'])}`",
                f"- Merged patch ID: `{_md(change['merged_patch_id'])}`",
                f"- Ancestry source SHA: `{_md(change['ancestry']['source_sha'])}`",
                f"- Ancestry ancestor SHA: `{_md(change['ancestry']['ancestor_sha'])}`",
                f"- Ancestry descendant SHA: `{_md(change['ancestry']['descendant_sha'])}`",
                f"- Ancestry check: {_md(change['ancestry']['check'])}",
                f"- Ancestry exit code: {_md(change['ancestry']['exit_code'])}",
                f"- Local test evidence: {_md(change['local_test_evidence'])}",
                "- Acceptance criteria:",
                *(f"  - {_md(criterion)}" for criterion in change["acceptance_criteria"]),
            ]
        )
    return "\n".join(lines)

def _source(document: dict[str, Any]) -> str:
    value = document["source"]
    lines = [
        "## Source",
        f"- Project SHA: `{_md(value['project_sha'])}`",
        f"- Manifest SHA-256: `{_md(value['manifest_sha256'])}`",
        f"- Builder log: {_md(value['builder_log'])}",
        "- Repository SHAs:",
    ]
    repositories = sorted(value["repositories"], key=lambda item: item["repository"])
    lines.extend(
        f"  - {_md(repository['repository'])}: `{_md(repository['sha'])}`"
        for repository in repositories
    )
    lines.append("- Local manifest SHA-256 values:")
    local_manifests = value["local_manifests"]
    if local_manifests:
        lines.extend(
            f"  - {_md(name)}: `{_md(digest)}`"
            for name, digest in sorted(local_manifests.items())
        )
    else:
        lines.append("  - None.")
    manifest = value["manifest_xml"]
    fence = "```"
    while fence in manifest:
        fence += "`"
    lines.extend(("- Revision-locked manifest:", f"{fence}xml", manifest, fence))
    return "\n".join(lines)

def _build_and_signing(document: dict[str, Any]) -> str:
    value = document["build_and_signing"]
    lines = [
        _section(
            "Build and signing",
            (
                ("Build result", value["build_result"]),
                ("Signing verified", value["signing_verified"]),
                ("Evidence", value["evidence"]),
            ),
        ),
        "- Checksum verification output:",
    ]
    if value["checksum_verification_output"]:
        lines.extend(
            f"  - {_md(line)}" for line in value["checksum_verification_output"]
        )
    else:
        lines.append("  - Not run.")
    return "\n".join(lines)

def _artifacts(document: dict[str, Any]) -> str:
    lines = ["## Artifacts"]
    if not document["artifacts"]:
        return "\n".join((*lines, "- Not produced."))
    for artifact in sorted(document["artifacts"], key=lambda item: item["name"]):
        lines.extend(
            [
                f"### {_md(artifact['name'])}",
                f"- Size: {_md(artifact['size'])} bytes",
                f"- SHA-256: `{_md(artifact['sha256'])}`",
                f"- Checksum verification: {_md(artifact['checksum_verified'])}",
            ]
        )
    return "\n".join(lines)

def _deployment(document: dict[str, Any]) -> str:
    value = document["deployment_public_checks"]
    lines = [
        "## Deployment/public checks",
        f"- Deployment verified: {_md(value['deployment_verified'])}",
        f"- Installed build ID: {_md(value['installed_build_id'] or '(missing)')}",
    ]
    container = value.get("container")
    if container:
        lines.extend(
            (
                f"- Container healthy: {_md(container['healthy'])}",
                f"- Container build ID label: {_md(container['build_id_label'])}",
                f"- Container image label: {_md(container['image_label'])}",
            )
        )
    else:
        lines.append("- Container evidence: Not captured.")
    lines.extend(_carried_channels(value.get("carried_channels", [])))
    if not value["checks"]:
        lines.append("- Public checks: Not run.")
    for check in sorted(value["checks"], key=lambda item: (item["name"], item["url"])):
        lines.extend(
            [
                f"### {_md(check['name'])}",
                f"- URL: {_md(check['url'])}",
                f"- HTTP status: {_md(check['status'])}",
                f"- Evidence: {_md(check['evidence'])}",
            ]
        )
        if "build_id" in check:
            lines.append(f"- Identified build ID: {_md(check['build_id'])}")
        if "redirected" in check:
            lines.append(f"- Redirected: {_md(check['redirected'])}")
        if "artifact_name" in check:
            lines.append(f"- Artifact: {_md(check['artifact_name'])}")
    return "\n".join(lines)

def _carried_channels(entries: list[dict[str, Any]]) -> list[str]:
    if not entries:
        return ["- Carried channels: None."]
    lines = ["- Carried channels:"]
    for entry in sorted(entries, key=lambda item: item["channel"]):
        lines.append(
            f"  - {_md(entry['channel'])}: {_md(entry['build_id'])}, "
            f"routes unchanged: {_md(entry['routes_unchanged'])}"
        )
    return lines

def _proof_outcomes(document: dict[str, Any]) -> str:
    lines = ["## Proof outcomes"]
    proofs = sorted(document["proof_outcomes"], key=lambda item: (item["feature"], item["claim"]))
    labels = {
        "claim": "Claim",
        "trigger_and_setup": "Trigger and setup",
        "observable_evidence": "Observable evidence",
        "expected_outcome": "Expected outcome",
        "observed_outcome": "Observed outcome",
        "verdict": "Verdict",
        "limitations": "Limitations",
        "post_release_device_check": "Post-release device check",
        "restoration": "Restoration",
    }
    for proof in proofs:
        lines.append(f"### {_md(proof['feature'])}")
        lines.extend(f"- {label}: {_md(proof[field])}" for field, label in labels.items())
    return "\n".join(lines)

def _device_restoration(document: dict[str, Any]) -> str:
    value = document["device_restoration"]
    return _section(
        "Device restoration",
        (("Completed", value["completed"]), ("Evidence", value["evidence"])),
    )

def _known_gaps(document: dict[str, Any]) -> str:
    lines = ["## Known gaps/observations"]
    lines.extend(f"- {_md(value)}" for value in sorted(document["known_gaps_observations"]))
    if not document["known_gaps_observations"]:
        lines.append("- None recorded.")
    exclusion = document.get("accessibility_exclusion")
    if exclusion:
        lines.append(f"- Accessibility exclusion: {_md(exclusion)}")
    return "\n".join(lines)

def _section(title: str, values: tuple[tuple[str, Any], ...]) -> str:
    lines = [f"## {title}"]
    lines.extend(f"- {label}: {_md(value)}" for label, value in values)
    return "\n".join(lines)

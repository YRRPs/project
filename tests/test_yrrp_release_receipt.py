from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_ota.channel import Channel
from yrrp_release.receipt import render_receipt, write_receipt

BUILD_ID = "20261008-123456"
SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
MANIFEST_XML = '<manifest><project name="YRRPs/android_vendor_extra" path="vendor/extra" revision="a" /></manifest>\n'
MANIFEST_DIGEST = hashlib.sha256(MANIFEST_XML.encode()).hexdigest()
ROOMSERVICE_XML = '<manifest><project name="LineageOS/android_device_oneplus_salami" path="device/oneplus/salami" /></manifest>\n'
ROOMSERVICE_DIGEST = hashlib.sha256(ROOMSERVICE_XML.encode()).hexdigest()
SOURCE_BUILD_ID = "20261001-123456"


def accepted_incremental_release(channel: str = "salami/vanilla") -> dict:
    parsed = Channel.parse(channel)
    target_files = parsed.target_files_name(BUILD_ID)
    full_ota = parsed.full_ota_name(BUILD_ID)
    incremental = parsed.incremental_ota_name(SOURCE_BUILD_ID, BUILD_ID)
    install = f"https://ota.yimura.dev/{parsed.install_dir(BUILD_ID)}"
    return {
        "release_identity": {
            "build_id": BUILD_ID,
            "channel": channel,
            "completion": "2026-10-08T14:00:00Z",
            "overall_result": "SUCCESS",
            "approval": "Build and release",
        },
        "selected_changes": [
            {
                "pr_url": "https://github.com/YRRPs/android_frameworks_base/pull/42",
                "repository": "frameworks/base",
                "branch": "feature/pulse",
                "base_sha": SHA_B,
                "tested_head_sha": SHA_A,
                "tested_patch_id": "c" * 40,
                "merged_sha": SHA_A,
                "merged_patch_id": "c" * 40,
                "ancestry": {
                    "source_sha": SHA_A,
                    "ancestor_sha": SHA_A,
                    "descendant_sha": SHA_A,
                    "check": "git merge-base --is-ancestor",
                    "exit_code": 0,
                },
                "local_test_evidence": "Focused SystemUI tests passed locally.",
                "acceptance_criteria": ["Pulse renders after playback starts."],
            }
        ],
        "source": {
            "project_sha": SHA_B,
            "repositories": [
                {"repository": "frameworks/base", "sha": SHA_A},
            ],
            "manifest_sha256": MANIFEST_DIGEST,
            "manifest_xml": MANIFEST_XML,
            "local_manifests": {"roomservice.xml": ROOMSERVICE_DIGEST},
            "builder_log": "/opt/android/logs/build-20261008-123456.log",
        },
        "build_and_signing": {
            "build_result": "SUCCESS",
            "signing_verified": True,
            "evidence": "OTA and target-files signatures verified.",
            "checksum_verification_output": [
                f"{target_files}: OK",
                f"{full_ota}: OK",
                f"{incremental}: OK",
            ],
        },
        "artifacts": [
            {
                "name": target_files,
                "size": 223456789,
                "sha256": DIGEST_A,
                "checksum_verified": True,
            },
            {
                "name": full_ota,
                "size": 123456789,
                "sha256": DIGEST_B,
                "checksum_verified": True,
            },
            {
                "name": parsed.checksums_name(BUILD_ID),
                "size": 512,
                "sha256": "c" * 64,
                "checksum_verified": True,
            },
            {
                "name": incremental,
                "size": 23456789,
                "sha256": "d" * 64,
                "checksum_verified": True,
            },
        ],
        "deployment_public_checks": {
            "deployment_verified": True,
            "installed_build_id": BUILD_ID,
            "container": {
                "healthy": True,
                "build_id_label": BUILD_ID,
                "image_label": "true",
            },
            "carried_channels": [],
            "checks": [
                {
                    "name": "healthz",
                    "url": "https://ota.yimura.dev/healthz",
                    "status": 200,
                    "evidence": "Health endpoint returned healthy.",
                },
                {
                    "name": "updates metadata",
                    "url": f"https://ota.yimura.dev/{parsed.updates_full_path}",
                    "status": 200,
                    "build_id": BUILD_ID,
                    "evidence": "Metadata identifies the installed build.",
                },
                {
                    "name": "stale fallback",
                    "url": f"https://ota.yimura.dev/{parsed.updates_incremental_path('1')}",
                    "status": 200,
                    "redirected": False,
                    "artifact_name": full_ota,
                    "build_id": BUILD_ID,
                    "evidence": "Legacy route returned the current full artifact directly.",
                },
                {
                    "name": "install listing",
                    "url": f"{install}/",
                    "status": 200,
                    "build_id": BUILD_ID,
                    "evidence": "Install listing returned all release files.",
                },
                {
                    "name": "full OTA range",
                    "url": f"{install}/{full_ota}",
                    "status": 206,
                    "artifact_name": full_ota,
                    "evidence": "Byte range returned partial content.",
                },
                {
                    "name": "incremental OTA range",
                    "url": f"{install}/{incremental}",
                    "status": 206,
                    "artifact_name": incremental,
                    "evidence": "Incremental byte range returned partial content.",
                },
            ],
        },
        "proof_outcomes": [
            {
                "feature": "Pulse",
                "claim": "Pulse appears while music plays.",
                "trigger_and_setup": "Install the release and start local audio playback.",
                "observable_evidence": "Device recording shows bars responding to audio.",
                "expected_outcome": "Bars animate while playback remains active.",
                "observed_outcome": "Bars animated until playback stopped.",
                "verdict": "PROVEN",
                "limitations": "Observed with one local media application.",
                "post_release_device_check": "Verified on the installed release build.",
                "restoration": "Playback stopped and SystemUI returned to idle.",
            }
        ],
        "device_restoration": {
            "completed": True,
            "evidence": "Playback stopped; device returned to the home screen.",
        },
        "known_gaps_observations": [
            "TalkBack/accessibility acceptance excluded for this personal ROM."
        ],
        "accessibility_exclusion": "Personal-ROM scope excludes TalkBack/accessibility acceptance.",
    }


class ReceiptRenderingTest(unittest.TestCase):
    def test_renders_deterministic_sections_and_incremental_release(self) -> None:
        document = accepted_incremental_release()
        first = render_receipt(document)
        second = render_receipt(copy.deepcopy(document))
        self.assertEqual(first, second)
        headings = [
            "## Release identity",
            "## Selected changes",
            "## Source",
            "## Build and signing",
            "## Artifacts",
            "## Deployment/public checks",
            "## Proof outcomes",
            "## Device restoration",
            "## Known gaps/observations",
        ]
        self.assertEqual(headings, [line for line in first.splitlines() if line.startswith("## ")])
        self.assertIn("incremental OTA", first)
        self.assertIn("Personal-ROM scope excludes TalkBack/accessibility acceptance.", first)

    def test_escapes_untrusted_markdown_without_tables(self) -> None:
        document = accepted_incremental_release()
        document["proof_outcomes"][0]["observed_outcome"] = "Line one\n# forged heading | cell <tag>"
        rendered = render_receipt(document)
        self.assertNotIn("\n# forged heading", rendered)
        self.assertNotIn("<tag>", rendered)
        self.assertNotIn("| ---", rendered)
        self.assertIn("&lt;tag&gt;", rendered)

    def test_rejects_missing_top_level_sections(self) -> None:
        for section in (
            "release_identity",
            "selected_changes",
            "source",
            "build_and_signing",
            "artifacts",
            "deployment_public_checks",
            "proof_outcomes",
            "device_restoration",
            "known_gaps_observations",
        ):
            document = accepted_incremental_release()
            del document[section]
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, section):
                render_receipt(document)

    def test_requires_every_proof_field_and_exact_verdict(self) -> None:
        required = (
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
        for field in required:
            document = accepted_incremental_release()
            del document["proof_outcomes"][0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                render_receipt(document)
        for verdict in ("proven", "PASS", "UNKNOWN"):
            document = accepted_incremental_release()
            document["proof_outcomes"][0]["verdict"] = verdict
            with self.subTest(verdict=verdict), self.assertRaisesRegex(ValueError, "verdict"):
                render_receipt(document)

    def test_success_requires_all_release_verification(self) -> None:
        mutations = (
            ("failed proof", lambda value: value["proof_outcomes"][0].update(verdict="FAILED")),
            ("unproven proof", lambda value: value["proof_outcomes"][0].update(verdict="UNPROVEN")),
            ("signing", lambda value: value["build_and_signing"].update(signing_verified=False)),
            ("checksum", lambda value: value["artifacts"][0].update(checksum_verified=False)),
            ("deployment", lambda value: value["deployment_public_checks"].update(deployment_verified=False)),
            ("identity missing", lambda value: value["deployment_public_checks"].update(installed_build_id="")),
            ("identity mismatch", lambda value: value["deployment_public_checks"].update(installed_build_id="20261008-123457")),
        )
        for label, mutate in mutations:
            document = accepted_incremental_release()
            mutate(document)
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "SUCCESS"):
                render_receipt(document)

    def test_failed_receipt_may_record_failed_and_unproven_claims(self) -> None:
        document = accepted_incremental_release()
        document["release_identity"]["overall_result"] = "FAILED"
        document["proof_outcomes"][0]["verdict"] = "UNPROVEN"
        document["build_and_signing"]["signing_verified"] = False
        document["deployment_public_checks"]["deployment_verified"] = False
        document["deployment_public_checks"]["installed_build_id"] = ""
        self.assertIn("UNPROVEN", render_receipt(document))

    def test_rejects_placeholders_invalid_values_and_duplicates(self) -> None:
        cases = []
        for value in ("TBD", "TODO: collect after build", "unknown until device check"):
            placeholder = accepted_incremental_release()
            placeholder["source"]["builder_log"] = value
            cases.append(("placeholder", placeholder))
        bad_sha = accepted_incremental_release()
        bad_sha["selected_changes"][0]["merged_sha"] = "not-a-sha"
        cases.append(("SHA", bad_sha))
        bad_url = accepted_incremental_release()
        bad_url["selected_changes"][0]["pr_url"] = "file:///etc/passwd"
        cases.append(("URL", bad_url))
        bad_type = accepted_incremental_release()
        bad_type["artifacts"][0]["size"] = "123"
        cases.append(("size", bad_type))
        duplicate_pr = accepted_incremental_release()
        duplicate_pr["selected_changes"].append(copy.deepcopy(duplicate_pr["selected_changes"][0]))
        cases.append(("duplicate PR", duplicate_pr))
        duplicate_repo = accepted_incremental_release()
        duplicate_repo["source"]["repositories"].append(copy.deepcopy(duplicate_repo["source"]["repositories"][0]))
        cases.append(("duplicate repository", duplicate_repo))
        duplicate_proof = accepted_incremental_release()
        duplicate_proof["proof_outcomes"].append(copy.deepcopy(duplicate_proof["proof_outcomes"][0]))
        cases.append(("duplicate proof", duplicate_proof))
        for message, document in cases:
            with self.subTest(message=message), self.assertRaisesRegex((ValueError, TypeError), message):
                render_receipt(document)

    def test_rejects_unknown_keys_at_every_object_level(self) -> None:
        paths = (
            (),
            ("release_identity",),
            ("selected_changes", 0),
            ("selected_changes", 0, "ancestry"),
            ("source",),
            ("source", "repositories", 0),
            ("build_and_signing",),
            ("artifacts", 0),
            ("deployment_public_checks",),
            ("deployment_public_checks", "checks", 0),
            ("proof_outcomes", 0),
            ("device_restoration",),
        )
        for path in paths:
            document = accepted_incremental_release()
            target = document
            for part in path:
                target = target[part]
            target["unexpected"] = "value"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unknown"):
                render_receipt(document)

    def test_rejects_missing_keys_at_every_object_level(self) -> None:
        paths = (
            (("release_identity",), "completion"),
            (("selected_changes", 0), "tested_patch_id"),
            (("selected_changes", 0), "merged_patch_id"),
            (("selected_changes", 0), "acceptance_criteria"),
            (("source",), "manifest_xml"),
            (("source",), "local_manifests"),
            (("source",), "builder_log"),
            (("source", "repositories", 0), "sha"),
            (("build_and_signing",), "evidence"),
            (("artifacts", 0), "checksum_verified"),
            (("deployment_public_checks",), "checks"),
            (("deployment_public_checks", "checks", 0), "evidence"),
            (("device_restoration",), "evidence"),
        )
        for path, key in paths:
            document = accepted_incremental_release()
            target = document
            for part in path:
                target = target[part]
            del target[key]
            with self.subTest(path=path, key=key), self.assertRaisesRegex(ValueError, "missing"):
                render_receipt(document)

    def test_failed_prebuild_receipt_allows_not_produced_and_not_run_sections(self) -> None:
        document = accepted_incremental_release()
        document["release_identity"]["overall_result"] = "FAILED"
        document["build_and_signing"].update(
            build_result="FAILED",
            signing_verified=False,
            checksum_verification_output=[],
        )
        document["build_and_signing"]["evidence"] = "Builder preflight failed before compilation."
        document["artifacts"] = []
        document["deployment_public_checks"] = {
            "deployment_verified": False,
            "installed_build_id": "",
            "checks": [],
        }
        document["proof_outcomes"][0]["verdict"] = "UNPROVEN"
        document["known_gaps_observations"] = ["Build did not start; runtime proof was not run."]
        rendered = render_receipt(document)
        self.assertIn("- Not produced.", rendered)
        self.assertIn("- Public checks: Not run.", rendered)
        self.assertIn("Build did not start", rendered)

    def test_failed_midbuild_receipt_allows_no_deployment(self) -> None:
        document = accepted_incremental_release()
        document["release_identity"]["overall_result"] = "FAILED"
        document["build_and_signing"].update(
            build_result="FAILED",
            signing_verified=False,
            checksum_verification_output=[],
        )
        document["artifacts"] = []
        document["deployment_public_checks"] = {
            "deployment_verified": False,
            "installed_build_id": "",
            "checks": [],
        }
        document["proof_outcomes"][0]["verdict"] = "FAILED"
        document["known_gaps_observations"] = ["Compilation failed; no artifact was produced."]
        self.assertIn("Compilation failed", render_receipt(document))

    def test_placeholder_rules_reject_markers_but_preserve_honest_unknown_observation(self) -> None:
        for marker in (
            "log TODO collect",
            "evidence is TBD",
            "status pending",
            "N/A",
            "not-applicable",
            "{{ builder_log }}",
            "${BUILD_LOG}",
        ):
            document = accepted_incremental_release()
            document["source"]["builder_log"] = marker
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "placeholder"):
                render_receipt(document)
        document = accepted_incremental_release()
        document["known_gaps_observations"] = [
            "Failure cause unknown after available diagnostics.",
            "Additional hardware coverage remains pending owner availability.",
        ]
        rendered = render_receipt(document)
        self.assertIn("cause unknown", rendered)
        self.assertIn("remains pending", rendered)

    def test_rejects_duplicate_list_identities(self) -> None:
        duplicate_criteria = accepted_incremental_release()
        duplicate_criteria["selected_changes"][0]["acceptance_criteria"].append(
            duplicate_criteria["selected_changes"][0]["acceptance_criteria"][0]
        )
        duplicate_observation = accepted_incremental_release()
        duplicate_observation["known_gaps_observations"].append(
            duplicate_observation["known_gaps_observations"][0]
        )
        duplicate_check = accepted_incremental_release()
        repeated = copy.deepcopy(duplicate_check["deployment_public_checks"]["checks"][0])
        repeated["url"] = "https://ota.yimura.dev/healthz?duplicate=1"
        duplicate_check["deployment_public_checks"]["checks"].append(repeated)
        for label, document in (
            ("acceptance criteria", duplicate_criteria),
            ("observation", duplicate_observation),
            ("deployment check name", duplicate_check),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "duplicate"):
                render_receipt(document)

    def test_rejects_malformed_https_authorities(self) -> None:
        for url in (
            "https://host:bad/path",
            "https://user@host/path",
            "https:///missing-host",
            "https://host:70000/path",
            "https://bad host/path",
            "https://host/path\nnext",
        ):
            document = accepted_incremental_release()
            document["selected_changes"][0]["pr_url"] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "URL"):
                render_receipt(document)

    def test_success_allows_project_only_release_without_android_repositories(self) -> None:
        document = accepted_incremental_release()
        change = document["selected_changes"][0]
        change["repository"] = "project"
        change["ancestry"] = {
            "source_sha": SHA_B,
            "ancestor_sha": change["merged_sha"],
            "descendant_sha": SHA_B,
            "check": "git merge-base --is-ancestor",
            "exit_code": 0,
        }
        document["source"]["repositories"] = []
        self.assertIn("### project", render_receipt(document))

    def test_success_allows_multiple_included_prs_in_one_repository_final_head(self) -> None:
        document = accepted_incremental_release()
        earlier = copy.deepcopy(document["selected_changes"][0])
        earlier.update(
            pr_url="https://github.com/YRRPs/android_frameworks_base/pull/41",
            base_sha="c" * 40,
            tested_head_sha="d" * 40,
            merged_sha="e" * 40,
            ancestry={
                "source_sha": SHA_A,
                "ancestor_sha": "e" * 40,
                "descendant_sha": SHA_A,
                "check": "git merge-base --is-ancestor",
                "exit_code": 0,
            },
        )
        document["selected_changes"].append(earlier)
        rendered = render_receipt(document)
        self.assertIn("pull/41", rendered)
        self.assertIn("pull/42", rendered)

    def test_source_embeds_revision_locked_manifest_matching_digest(self) -> None:
        document = accepted_incremental_release()
        rendered = render_receipt(document)
        self.assertIn(MANIFEST_XML.strip(), rendered)

        document["source"]["manifest_xml"] = MANIFEST_XML.replace("vendor/extra", "vendor/other")
        with self.assertRaisesRegex(ValueError, "manifest"):
            render_receipt(document)

    def test_source_records_validated_local_manifest_hashes(self) -> None:
        document = accepted_incremental_release()
        rendered = render_receipt(document)
        self.assertIn("roomservice.xml", rendered)
        self.assertIn(ROOMSERVICE_DIGEST, rendered)

        for name, digest in (("../escape.xml", ROOMSERVICE_DIGEST), ("roomservice.txt", ROOMSERVICE_DIGEST), ("roomservice.xml", "bad")):
            document = accepted_incremental_release()
            document["source"]["local_manifests"] = {name: digest}
            with self.subTest(name=name, digest=digest), self.assertRaisesRegex(ValueError, "local manifest"):
                render_receipt(document)

    def test_success_requires_tested_and_merged_patch_equivalence(self) -> None:
        document = accepted_incremental_release()
        document["selected_changes"][0]["merged_patch_id"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "tested.*patch|patch.*tested"):
            render_receipt(document)

    def test_success_requires_change_ancestry_inclusion_evidence(self) -> None:
        mutations = (
            ("exit_code", 1),
            ("ancestor_sha", "e" * 40),
            ("descendant_sha", "e" * 40),
            ("source_sha", "e" * 40),
        )
        for field, value in mutations:
            document = accepted_incremental_release()
            document["selected_changes"][0]["ancestry"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "ancestry"):
                render_receipt(document)

    def test_success_rejects_selected_repository_missing_from_source(self) -> None:
        document = accepted_incremental_release()
        document["selected_changes"][0]["repository"] = "packages/apps/Settings"
        with self.assertRaisesRegex(ValueError, "source"):
            render_receipt(document)

    def test_success_requires_complete_signed_artifact_set(self) -> None:
        document = accepted_incremental_release()
        document["artifacts"] = [
            {
                "name": "notes.txt",
                "size": 12,
                "sha256": DIGEST_A,
                "checksum_verified": True,
            }
        ]
        with self.assertRaisesRegex(ValueError, "artifact"):
            render_receipt(document)

    def test_success_requires_exact_checksum_command_output(self) -> None:
        mutations = []
        missing = accepted_incremental_release()
        missing["build_and_signing"]["checksum_verification_output"].pop()
        mutations.append(("missing", missing))
        failed = accepted_incremental_release()
        failed["build_and_signing"]["checksum_verification_output"][0] = (
            f"lineage-23.2-salami-{BUILD_ID}-signed-target_files.zip: FAILED"
        )
        mutations.append(("failed", failed))
        extra = accepted_incremental_release()
        extra["build_and_signing"]["checksum_verification_output"].append("unlisted.zip: OK")
        mutations.append(("extra", extra))
        for label, document in mutations:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "checksum"):
                render_receipt(document)

    def test_success_validates_incremental_target_relationship(self) -> None:
        document = accepted_incremental_release()
        document["artifacts"][-1]["name"] = (
            "lineage-23.2-salami-20261001-123456-to-20261009-123456-signed-incremental-ota.zip"
        )
        with self.assertRaisesRegex(ValueError, "incremental"):
            render_receipt(document)

    def test_success_requires_named_public_checks_and_statuses(self) -> None:
        for missing_name in (
            "healthz",
            "updates metadata",
            "stale fallback",
            "install listing",
            "full OTA range",
            "incremental OTA range",
        ):
            document = accepted_incremental_release()
            document["deployment_public_checks"]["checks"] = [
                check
                for check in document["deployment_public_checks"]["checks"]
                if check["name"] != missing_name
            ]
            with self.subTest(name=missing_name), self.assertRaisesRegex(ValueError, "public check"):
                render_receipt(document)
        document = accepted_incremental_release()
        document["deployment_public_checks"]["checks"][0]["status"] = 500
        with self.assertRaisesRegex(ValueError, "public check"):
            render_receipt(document)

    def test_success_binds_public_check_names_to_expected_urls(self) -> None:
        mutations = (
            ("healthz", "https://ota.yimura.dev/other"),
            ("updates metadata", "https://ota.yimura.dev/api/v1/salami/updates"),
            ("stale fallback", "https://ota.yimura.dev/updates/salami/2.json"),
            ("install listing", f"https://ota.yimura.dev/install/salami/{BUILD_ID}/other/"),
            ("full OTA range", f"https://ota.yimura.dev/install/salami/{BUILD_ID}/wrong.zip"),
            ("incremental OTA range", f"https://ota.yimura.dev/install/salami/{BUILD_ID}/wrong-incremental.zip"),
        )
        for name, url in mutations:
            document = accepted_incremental_release()
            check = next(
                item for item in document["deployment_public_checks"]["checks"] if item["name"] == name
            )
            check["url"] = url
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "public check"):
                render_receipt(document)
        document = accepted_incremental_release()
        document["deployment_public_checks"]["checks"][0]["url"] = "https://example.com/healthz"
        with self.assertRaisesRegex(ValueError, "public check"):
            render_receipt(document)

    def test_public_check_schemas_reject_irrelevant_fields_by_name(self) -> None:
        additions = (
            ("healthz", "redirected", False),
            ("updates metadata", "artifact_name", "misleading.zip"),
            ("install listing", "redirected", False),
            ("full OTA range", "build_id", BUILD_ID),
            ("stale fallback", "unexpected", "value"),
            ("incremental OTA range", "build_id", BUILD_ID),
        )
        for name, key, value in additions:
            document = accepted_incremental_release()
            check = next(
                item
                for item in document["deployment_public_checks"]["checks"]
                if item["name"] == name
            )
            check[key] = value
            with self.subTest(name=name, key=key), self.assertRaisesRegex(ValueError, "field"):
                render_receipt(document)

    def test_success_requires_healthy_labeled_container_and_stale_fallback(self) -> None:
        mutations = (
            ("healthy", False),
            ("build_id_label", "20261008-123455"),
            ("image_label", "false"),
        )
        for field, value in mutations:
            document = accepted_incremental_release()
            document["deployment_public_checks"]["container"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "container"):
                render_receipt(document)
        for field, value in (("redirected", True), ("artifact_name", "wrong.zip")):
            document = accepted_incremental_release()
            stale = next(
                check
                for check in document["deployment_public_checks"]["checks"]
                if check["name"] == "stale fallback"
            )
            stale[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "stale fallback"):
                render_receipt(document)

    def test_success_rejects_extra_public_checks(self) -> None:
        document = accepted_incremental_release()
        document["deployment_public_checks"]["checks"].append(
            {
                "name": "diagnostic",
                "url": "https://ota.yimura.dev/diagnostic",
                "status": 500,
                "evidence": "Diagnostic failed.",
            }
        )
        with self.assertRaisesRegex(ValueError, "public check"):
            render_receipt(document)

    def test_validates_real_build_date_and_timezone_completion(self) -> None:
        for build_id in ("20261308-123456", "20260230-123456", "20261008-246000"):
            document = accepted_incremental_release()
            document["release_identity"]["build_id"] = build_id
            with self.subTest(build_id=build_id), self.assertRaisesRegex(ValueError, "build ID"):
                render_receipt(document)
        for completion in ("not-a-date", "2026-10-08T14:00:00"):
            document = accepted_incremental_release()
            document["release_identity"]["completion"] = completion
            with self.subTest(completion=completion), self.assertRaisesRegex(ValueError, "completion"):
                render_receipt(document)

    def test_completion_and_incremental_times_are_correlated(self) -> None:
        for completion in ("2026-10-08T12:34:55Z", "2027-10-08T12:34:56Z"):
            document = accepted_incremental_release()
            document["release_identity"]["completion"] = completion
            with self.subTest(completion=completion), self.assertRaisesRegex(ValueError, "completion"):
                render_receipt(document)
        document = accepted_incremental_release()
        incremental = document["artifacts"][-1]
        incremental["name"] = (
            f"lineage-23.2-salami-20261009-123456-to-{BUILD_ID}-signed-incremental-ota.zip"
        )
        document["build_and_signing"]["checksum_verification_output"][-1] = (
            incremental["name"] + ": OK"
        )
        route = next(
            check
            for check in document["deployment_public_checks"]["checks"]
            if check["name"] == "incremental OTA range"
        )
        route["url"] = f"https://ota.yimura.dev/install/salami/{BUILD_ID}/{incremental['name']}"
        with self.assertRaisesRegex(ValueError, "older"):
            render_receipt(document)

    def test_rejects_deceptive_unicode_format_controls(self) -> None:
        for value in ("safe‮evil", "zero​width"):
            document = accepted_incremental_release()
            document["proof_outcomes"][0]["observed_outcome"] = value
            with self.subTest(value=repr(value)), self.assertRaisesRegex(ValueError, "Unicode"):
                render_receipt(document)
        document = accepted_incremental_release()
        document["proof_outcomes"][0]["observed_outcome"] = "正常な Unicode evidence"
        self.assertIn("正常な Unicode evidence", render_receipt(document))

    def test_rejects_control_characters_and_surrogates_before_rendering(self) -> None:
        for value in ("nul\x00byte", "escape\x1bsequence", "surrogate\ud800value"):
            document = accepted_incremental_release()
            document["proof_outcomes"][0]["observed_outcome"] = value
            with self.subTest(value=repr(value)), self.assertRaisesRegex(ValueError, "Unicode"):
                render_receipt(document)

    def test_normalizes_newline_and_tab_whitespace_in_rendered_text(self) -> None:
        document = accepted_incremental_release()
        document["proof_outcomes"][0]["observed_outcome"] = (
            "normal Unicode 日本語\nsecond line\tcontinued"
        )
        rendered = render_receipt(document)
        self.assertIn("normal Unicode 日本語 second line continued", rendered)
        self.assertNotIn("\t", rendered)

    def test_accessibility_exclusion_is_recorded_not_required_as_proof(self) -> None:
        rendered = render_receipt(accepted_incremental_release())
        self.assertIn("TalkBack/accessibility", rendered)
        self.assertNotIn("Missing proof", rendered)


    def test_vanilla_fixture_keeps_legacy_names_and_routes(self) -> None:
        document = accepted_incremental_release()
        names = {artifact["name"] for artifact in document["artifacts"]}
        self.assertIn(f"lineage-23.2-salami-{BUILD_ID}-signed-ota.zip", names)
        urls = {check["url"] for check in document["deployment_public_checks"]["checks"]}
        self.assertIn("https://ota.yimura.dev/updates/salami.json", urls)
        self.assertIn("https://ota.yimura.dev/updates/salami/1.json", urls)
        self.assertIn(f"https://ota.yimura.dev/install/salami/{BUILD_ID}/", urls)

    def test_gapps_success_receipt_uses_gapps_names_and_routes(self) -> None:
        rendered = render_receipt(accepted_incremental_release(channel="salami/gapps"))
        self.assertIn("- Channel: salami/gapps", rendered)
        self.assertIn(f"lineage-23.2-salami-gapps-{BUILD_ID}-signed-ota.zip", rendered)
        self.assertIn("/updates/salami/gapps.json", rendered)
        self.assertIn("/updates/salami/gapps/1.json", rendered)
        self.assertIn(f"/install/salami/gapps/{BUILD_ID}/", rendered)

    def test_gapps_success_rejects_vanilla_names_and_routes(self) -> None:
        vanilla = accepted_incremental_release()
        mutations = (
            ("artifacts", "artifact"),
            ("deployment_public_checks", "public check"),
        )
        for section, message in mutations:
            document = accepted_incremental_release(channel="salami/gapps")
            document[section] = copy.deepcopy(vanilla[section])
            if section == "artifacts":
                document["build_and_signing"] = copy.deepcopy(vanilla["build_and_signing"])
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, message):
                render_receipt(document)

    def test_vanilla_success_rejects_gapps_incremental_name(self) -> None:
        document = accepted_incremental_release()
        gapps_name = Channel.parse("salami/gapps").incremental_ota_name(SOURCE_BUILD_ID, BUILD_ID)
        document["artifacts"][-1]["name"] = gapps_name
        document["build_and_signing"]["checksum_verification_output"][-1] = f"{gapps_name}: OK"
        with self.assertRaisesRegex(ValueError, "incremental"):
            render_receipt(document)

    def test_missing_channel_defaults_to_vanilla(self) -> None:
        document = accepted_incremental_release()
        del document["release_identity"]["channel"]
        del document["deployment_public_checks"]["carried_channels"]
        self.assertIn("- Channel: salami/vanilla", render_receipt(document))

    def test_old_input_without_carried_channels_renders_not_recorded(self) -> None:
        document = accepted_incremental_release()
        del document["release_identity"]["channel"]
        del document["deployment_public_checks"]["carried_channels"]
        rendered = render_receipt(document)
        self.assertIn("- Carried channels: Not recorded.", rendered)
        self.assertIn(f"- Container build ID label: {BUILD_ID}\n", rendered)

    def test_empty_carried_channels_renders_none(self) -> None:
        rendered = render_receipt(accepted_incremental_release())
        self.assertIn("- Carried channels: None.", rendered)
        self.assertNotIn("Not recorded", rendered)

    def test_new_style_success_requires_carried_channels(self) -> None:
        document = accepted_incremental_release(channel="salami/gapps")
        del document["deployment_public_checks"]["carried_channels"]
        with self.assertRaisesRegex(ValueError, "carried_channels"):
            render_receipt(document)
        document["release_identity"]["overall_result"] = "FAILED"
        self.assertIn("- Carried channels: Not recorded.", render_receipt(document))

    def test_new_style_container_line_names_checked_label(self) -> None:
        rendered = render_receipt(accepted_incremental_release(channel="salami/gapps"))
        self.assertIn(
            "- Container build ID label: io.yrrp.ota.channel.salami.gapps.build-id = "
            f"{BUILD_ID}\n",
            rendered,
        )

    def test_rejects_invalid_release_channel(self) -> None:
        for channel in ("salami/other", "salami", "", 7):
            document = accepted_incremental_release()
            document["release_identity"]["channel"] = channel
            with self.subTest(channel=channel), self.assertRaises((TypeError, ValueError)):
                render_receipt(document)

    def test_records_carried_channels(self) -> None:
        document = accepted_incremental_release(channel="salami/gapps")
        document["deployment_public_checks"]["carried_channels"] = [
            {"channel": "salami/vanilla", "build_id": "20261007-120000", "routes_unchanged": True}
        ]
        rendered = render_receipt(document)
        self.assertIn("- Carried channels:", rendered)
        self.assertIn("  - salami/vanilla: 20261007-120000, routes unchanged: True", rendered)

    def test_carried_channel_with_changed_routes_is_rejected(self) -> None:
        document = accepted_incremental_release(channel="salami/gapps")
        document["deployment_public_checks"]["carried_channels"] = [
            {"channel": "salami/vanilla", "build_id": "20261007-120000", "routes_unchanged": False}
        ]
        with self.assertRaisesRegex(ValueError, "carried channel.*routes changed"):
            render_receipt(document)
        document["release_identity"]["overall_result"] = "FAILED"
        self.assertIn("routes unchanged: False", render_receipt(document))

    def test_rejects_malformed_carried_channels(self) -> None:
        carried = {"channel": "salami/vanilla", "build_id": "20261007-120000", "routes_unchanged": True}
        mutations = (
            ("not a list", {"channel": "salami/vanilla"}),
            ("released channel", [dict(carried, channel="salami/gapps")]),
            ("unknown channel", [dict(carried, channel="salami/other")]),
            ("duplicate", [carried, dict(carried)]),
            ("bad build ID", [dict(carried, build_id="2026")]),
            ("non-boolean", [dict(carried, routes_unchanged="yes")]),
            ("missing key", [{"channel": "salami/vanilla", "build_id": "20261007-120000"}]),
            ("unknown key", [dict(carried, extra=1)]),
        )
        for label, value in mutations:
            document = accepted_incremental_release(channel="salami/gapps")
            document["release_identity"]["overall_result"] = "FAILED"
            document["deployment_public_checks"]["carried_channels"] = value
            with self.subTest(label=label), self.assertRaises((TypeError, ValueError)):
                render_receipt(document)


class ReceiptPersistenceTest(unittest.TestCase):
    def test_public_writer_has_no_root_or_output_override(self) -> None:
        with self.assertRaises(TypeError):
            write_receipt(accepted_incremental_release(), root=Path("elsewhere"))

    def test_creates_private_parent_and_exclusive_private_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root):
                path = write_receipt(accepted_incremental_release())
            self.assertEqual(root / ".claude" / "releases" / f"{BUILD_ID}.md", path)
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(render_receipt(accepted_incremental_release()), path.read_text())

    def test_rejects_invalid_build_id_and_cannot_escape_destination(self) -> None:
        for build_id in ("../../escape", "20261008", "20261008-123456.md", "20261008-12345x"):
            document = accepted_incremental_release()
            document["release_identity"]["build_id"] = build_id
            with tempfile.TemporaryDirectory() as directory, self.subTest(build_id=build_id):
                with patch("yrrp_release.receipt.PROJECT_ROOT", Path(directory)):
                    with self.assertRaisesRegex(ValueError, "build ID"):
                        write_receipt(document)

    def test_gapps_receipt_path_carries_channel_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root):
                vanilla = write_receipt(accepted_incremental_release())
                gapps = write_receipt(accepted_incremental_release(channel="salami/gapps"))
                with self.assertRaisesRegex(FileExistsError, "already exists"):
                    write_receipt(accepted_incremental_release(channel="salami/gapps"))
            releases = root / ".claude" / "releases"
            self.assertEqual(releases / f"{BUILD_ID}.md", vanilla)
            self.assertEqual(releases / f"gapps-{BUILD_ID}.md", gapps)
            self.assertIn("salami/gapps", gapps.read_text())
            self.assertIn("/updates/salami/gapps.json", gapps.read_text())
            self.assertEqual(["20261008-123456.md", "gapps-20261008-123456.md"], sorted(os.listdir(releases)))

    def test_refuses_overwrite_without_changing_existing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root):
                path = write_receipt(accepted_incremental_release())
                original = path.read_bytes()
                with self.assertRaisesRegex(FileExistsError, "already exists"):
                    write_receipt(accepted_incremental_release())
            self.assertEqual(original, path.read_bytes())

    def test_refuses_release_directory_symlink_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / ".claude").mkdir()
            (root / ".claude" / "releases").symlink_to(outside, target_is_directory=True)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root):
                with self.assertRaises(OSError):
                    write_receipt(accepted_incremental_release())
            self.assertEqual([], list(Path(outside).iterdir()))

    def test_directory_swap_cannot_redirect_publication_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            releases = root / ".claude" / "releases"
            releases.mkdir(parents=True)
            held = root / ".claude" / "held-releases"
            real_link = os.link

            def swap_then_link(*args, **kwargs):
                releases.rename(held)
                releases.symlink_to(outside, target_is_directory=True)
                return real_link(*args, **kwargs)

            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.link", side_effect=swap_then_link
            ):
                with self.assertRaisesRegex(OSError, "changed"):
                    write_receipt(accepted_incremental_release())
            self.assertEqual([], list(Path(outside).iterdir()))
            self.assertEqual([], [path for path in held.iterdir() if path.is_file()])

    def test_claude_directory_swap_is_detected_without_outside_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            releases = root / ".claude" / "releases"
            releases.mkdir(parents=True)
            held_claude = root / "held-claude"
            real_link = os.link

            def swap_then_link(*args, **kwargs):
                (root / ".claude").rename(held_claude)
                (root / ".claude").symlink_to(outside, target_is_directory=True)
                return real_link(*args, **kwargs)

            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.link", side_effect=swap_then_link
            ):
                with self.assertRaisesRegex(OSError, "changed"):
                    write_receipt(accepted_incremental_release())
            self.assertEqual([], list(Path(outside).iterdir()))
            held_releases = held_claude / "releases"
            self.assertEqual([], [path for path in held_releases.iterdir() if path.is_file()])

    def test_failure_leaves_no_json_or_temporary_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = accepted_incremental_release()
            document["source"]["manifest_sha256"] = "bad"
            with patch("yrrp_release.receipt.PROJECT_ROOT", root):
                with self.assertRaises(ValueError):
                    write_receipt(document)
            files = [path for path in root.rglob("*") if path.is_file()]
            self.assertEqual([], files)

    def test_atomic_publish_failure_removes_temporary_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.link", side_effect=OSError("publish failed")
            ):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    write_receipt(accepted_incremental_release())
            files = [path for path in root.rglob("*") if path.is_file()]
            self.assertEqual([], files)

    def test_creation_and_publication_fsync_parent_directories_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            real_fsync = os.fsync
            real_link = os.link
            real_unlink = os.unlink

            def track_fsync(descriptor):
                events.append(("fsync", descriptor))
                return real_fsync(descriptor)

            def track_link(*args, **kwargs):
                events.append(("link", args[0]))
                return real_link(*args, **kwargs)

            def track_unlink(path, *args, **kwargs):
                if str(path).endswith(".tmp"):
                    events.append(("unlink-temp", path))
                return real_unlink(path, *args, **kwargs)

            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.fsync", side_effect=track_fsync
            ), patch("yrrp_release.receipt.os.link", side_effect=track_link), patch(
                "yrrp_release.receipt.os.unlink", side_effect=track_unlink
            ):
                write_receipt(accepted_incremental_release())
            kinds = [event[0] for event in events]
            link_index = kinds.index("link")
            unlink_index = kinds.index("unlink-temp")
            self.assertGreaterEqual(kinds.count("fsync"), 4)
            self.assertLess(link_index, unlink_index)
            self.assertEqual("fsync", kinds[-1])
            self.assertLess(unlink_index, len(kinds) - 1)

    def test_directory_fsync_failure_reports_error_without_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def fail_directory_fsync(descriptor):
                calls.append(descriptor)
                if len(calls) == 4:
                    raise OSError("directory fsync failed")

            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.fsync", side_effect=fail_directory_fsync
            ):
                with self.assertRaisesRegex(OSError, "directory fsync failed"):
                    write_receipt(accepted_incremental_release())
            releases = root / ".claude" / "releases"
            self.assertEqual(4, len(calls))
            self.assertTrue((releases / f"{BUILD_ID}.md").is_file())
            self.assertEqual([], list(releases.glob("*.tmp")))

    def test_permission_hardening_failure_leaves_no_final_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("yrrp_release.receipt.PROJECT_ROOT", root), patch(
                "yrrp_release.receipt.os.fchmod", side_effect=PermissionError("denied")
            ):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    write_receipt(accepted_incremental_release())
            files = [path for path in root.rglob("*") if path.is_file()]
            self.assertEqual([], files)


class ReceiptCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("yrrp_release_cli", SCRIPTS / "yrrp-release.py")
        assert spec and spec.loader
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def test_receipt_reads_json_file_without_consuming_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.json"
            source.write_text(json.dumps(accepted_incremental_release()))
            arguments = self.cli.parse_args(["receipt", "--input", str(source)])
            with patch.object(self.cli, "write_receipt", return_value=Path(".claude/releases/out.md")) as writer:
                result = self.cli.run(arguments)
            writer.assert_called_once()
            self.assertEqual(BUILD_ID, writer.call_args.args[0]["release_identity"]["build_id"])
            self.assertEqual({"receipt": ".claude/releases/out.md"}, result)
            self.assertTrue(source.exists())

    def test_receipt_preserves_file_input_when_rendering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.json"
            source.write_text(json.dumps(accepted_incremental_release()))
            arguments = self.cli.parse_args(["receipt", "--input", str(source)])
            with patch.object(self.cli, "write_receipt", side_effect=ValueError("invalid")):
                with self.assertRaisesRegex(ValueError, "invalid"):
                    self.cli.run(arguments)
            self.assertTrue(source.exists())

    def test_receipt_preserves_invalid_json_file_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.json"
            source.write_text("not JSON")
            arguments = self.cli.parse_args(["receipt", "--input", str(source)])
            with self.assertRaises(json.JSONDecodeError):
                self.cli.run(arguments)
            self.assertTrue(source.exists())

    def test_receipt_reads_json_from_stdin_without_persisting_input(self) -> None:
        arguments = self.cli.parse_args(["receipt", "--input", "-"])
        stream = io.StringIO(json.dumps(accepted_incremental_release()))
        with patch.object(self.cli, "write_receipt", return_value=Path(".claude/releases/out.md")) as writer, patch.object(sys, "stdin", stream):
            result = self.cli.run(arguments)
        writer.assert_called_once()
        self.assertEqual({"receipt": ".claude/releases/out.md"}, result)


if __name__ == "__main__":
    unittest.main()

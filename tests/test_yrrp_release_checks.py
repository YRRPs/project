from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_release.checks import (
    CHECK_TARGET_SCRIPT,
    REMOTE_CHECK_SCRIPT,
    match_check_to_source,
    parse_atest_result,
    start_check,
    validate_check_result,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST = "c" * 64
RUN_ID = "check-20261015-123456-a1b2c3d4"
SUCCESS = """
Summary
-------
ThemePickerRoboTests: Passed: 12, Failed: 0, Ignored: 0
12 Tests Passed
"""
ZERO = """
Summary
-------
ThemePickerRoboTests: Passed: 0, Failed: 0, Ignored: 0
0 Tests Passed
"""
COMPILE_ONLY = "Build completed successfully\n"


def valid_result() -> dict:
    return {
        "schema_version": 1,
        "run_id": RUN_ID,
        "workspace": "release",
        "device": "salami",
        "source": {
            "project_sha": SHA_A,
            "repositories": {"packages/apps/ThemePicker": SHA_B},
            "manifest_sha256": DIGEST,
        },
        "targets": [
            {
                "ids": ["m:ThemePicker"],
                "executor": "m",
                "argv": ["m", "ThemePicker"],
                "result": "passed",
                "exit_code": 0,
                "started_at": "2026-10-15T12:35:00Z",
                "completed_at": "2026-10-15T12:36:00Z",
            },
            {
                "ids": ["atest:ThemePickerRoboTests"],
                "executor": "atest",
                "argv": ["atest", "ThemePickerRoboTests"],
                "result": "passed",
                "exit_code": 0,
                "executed_tests": 12,
                "started_at": "2026-10-15T12:36:00Z",
                "completed_at": "2026-10-15T12:37:00Z",
            },
        ],
        "started_at": "2026-10-15T12:34:56Z",
        "completed_at": "2026-10-15T12:37:01Z",
        "log": f"/opt/android/out/yrrp-checks/{RUN_ID}/job.log",
        "result": "passed",
        "exit_code": 0,
        "failure_reason": None,
    }


class AtestResultTest(unittest.TestCase):
    def test_requires_positive_execution_count(self) -> None:
        self.assertEqual(12, parse_atest_result(SUCCESS))
        invalid = (
            ZERO,
            COMPILE_ONLY,
            "",
            SUCCESS + "Other: Passed: 1, Failed: 0, Ignored: 0\n",
        )
        for output in invalid:
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_atest_result(output)

    def test_counts_failed_and_ignored_tests_as_execution(self) -> None:
        output = "Suite: Passed: 2, Failed: 1, Ignored: 3\n"
        self.assertEqual(6, parse_atest_result(output))


class CheckResultValidationTest(unittest.TestCase):
    def test_accepts_strict_result_and_returns_copy(self) -> None:
        source = valid_result()
        validated = validate_check_result(source)
        self.assertEqual(source, validated)
        self.assertIsNot(source, validated)
        self.assertIsNot(source["targets"], validated["targets"])

    def test_rejects_unknown_or_missing_top_level_keys(self) -> None:
        for mutate in (
            lambda value: value.update({"unexpected": True}),
            lambda value: value.pop("device"),
        ):
            document = valid_result()
            mutate(document)
            with self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)

    def test_rejects_wrong_argv_duplicate_ids_and_zero_tests(self) -> None:
        mutations = []

        wrong_argv = valid_result()
        wrong_argv["targets"][0]["argv"] = ["sh", "-c", "id"]
        mutations.append(wrong_argv)

        duplicate = valid_result()
        duplicate["targets"][1]["ids"] = ["m:ThemePicker"]
        mutations.append(duplicate)

        zero = valid_result()
        zero["targets"][1]["executed_tests"] = 0
        mutations.append(zero)

        for document in mutations:
            with self.subTest(document=document), self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)

    def test_accepts_failure_before_any_target_starts(self) -> None:
        document = valid_result()
        document["targets"] = []
        document["result"] = "failed"
        document["exit_code"] = 1
        document["failure_reason"] = "source synchronization failed"
        self.assertEqual([], validate_check_result(document)["targets"])

    def test_accepts_failed_atest_before_test_execution(self) -> None:
        document = valid_result()
        document["result"] = "failed"
        document["exit_code"] = 1
        document["failure_reason"] = "atest compilation failed"
        document["targets"][1]["result"] = "failed"
        document["targets"][1]["exit_code"] = 1
        document["targets"][1]["executed_tests"] = 0
        self.assertEqual("failed", validate_check_result(document)["result"])

    def test_rejects_bad_timestamps_exit_codes_and_log_paths(self) -> None:
        documents = []
        reversed_time = valid_result()
        reversed_time["completed_at"] = "2026-10-15T12:00:00Z"
        documents.append(reversed_time)
        nonzero_pass = valid_result()
        nonzero_pass["targets"][0]["exit_code"] = 1
        documents.append(nonzero_pass)
        bad_log = valid_result()
        bad_log["log"] = "/tmp/check.log"
        documents.append(bad_log)
        for document in documents:
            with self.subTest(document=document), self.assertRaises((TypeError, ValueError)):
                validate_check_result(document)


class SourceMatchTest(unittest.TestCase):
    def test_matches_release_check_to_exact_source_and_targets(self) -> None:
        checked = match_check_to_source(
            valid_result(),
            {
                "project_sha": SHA_A,
                "repositories": {"packages/apps/ThemePicker": SHA_B},
                "manifest_sha256": DIGEST,
            },
            ("m:ThemePicker", "atest:ThemePickerRoboTests"),
        )
        self.assertEqual(RUN_ID, checked["run_id"])

    def test_rejects_empty_or_missing_required_targets(self) -> None:
        with self.assertRaises(ValueError):
            match_check_to_source(valid_result(), valid_result()["source"], ())
        with self.assertRaises(ValueError):
            match_check_to_source(
                valid_result(), valid_result()["source"], ("m:SystemUI",)
            )

    def test_rejects_isolated_failed_or_stale_evidence(self) -> None:
        documents = []
        isolated = valid_result()
        isolated["workspace"] = "isolated"
        documents.append(isolated)
        failed = valid_result()
        failed["result"] = "failed"
        failed["exit_code"] = 1
        failed["failure_reason"] = "compile failed"
        failed["targets"][0]["result"] = "failed"
        failed["targets"][0]["exit_code"] = 1
        documents.append(failed)
        for document in documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                match_check_to_source(
                    document,
                    valid_result()["source"],
                    ("m:ThemePicker",),
                )

        for field, changed in (
            ("project_sha", "d" * 40),
            ("manifest_sha256", "e" * 64),
            ("repositories", {"packages/apps/ThemePicker": "f" * 40}),
        ):
            prepared = copy.deepcopy(valid_result()["source"])
            prepared[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                match_check_to_source(valid_result(), prepared, ("m:ThemePicker",))


class CheckLaunchTest(unittest.TestCase):
    @patch("yrrp_release.checks.run_remote")
    @patch("yrrp_release.checks.verify_local_project")
    def test_start_check_sends_only_validated_json(self, verify, remote) -> None:
        remote.return_value = {
            "run_id": RUN_ID,
            "workspace": "isolated",
            "status": f"/opt/android/out/yrrp-checks/{RUN_ID}/status.json",
            "log": f"/opt/android/out/yrrp-checks/{RUN_ID}/job.log",
        }
        result = start_check(
            workspace="isolated",
            project_sha=SHA_A,
            repositories={"packages/apps/ThemePicker": SHA_B},
            device="salami",
            target_ids=["m:ThemePicker", "atest:ThemePickerRoboTests"],
        )
        verify.assert_called_once_with(SHA_A)
        self.assertEqual(
            {
                "workspace": "isolated",
                "project_sha": SHA_A,
                "repositories": {"packages/apps/ThemePicker": SHA_B},
                "device": "salami",
                "targets": ["m:ThemePicker", "atest:ThemePickerRoboTests"],
            },
            remote.call_args.args[1],
        )
        self.assertEqual(RUN_ID, result["run_id"])

    @patch("yrrp_release.checks.run_remote")
    @patch("yrrp_release.checks.verify_local_project")
    def test_invalid_request_never_contacts_builder(self, verify, remote) -> None:
        cases = (
            {"workspace": "other", "device": "salami", "targets": ["m:ThemePicker"]},
            {"workspace": "isolated", "device": "other", "targets": ["m:ThemePicker"]},
            {"workspace": "isolated", "device": "salami", "targets": ["m:Unknown"]},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                start_check(
                    workspace=case["workspace"],
                    project_sha=SHA_A,
                    repositories={},
                    device=case["device"],
                    target_ids=case["targets"],
                )
        verify.assert_not_called()
        remote.assert_not_called()

    def test_remote_check_contract_uses_shared_lock_and_detached_screen(self) -> None:
        self.assertIn("/home/android/.yrrp-build-launch.lock", REMOTE_CHECK_SCRIPT)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", REMOTE_CHECK_SCRIPT)
        self.assertIn('"screen", "-L", "-Logfile"', REMOTE_CHECK_SCRIPT)
        self.assertIn('"-dmS", run_id', REMOTE_CHECK_SCRIPT)
        self.assertIn('atomic_json(result_path', REMOTE_CHECK_SCRIPT)

    def test_isolated_mounts_do_not_share_release_source_or_output(self) -> None:
        self.assertIn(
            "/mnt/fast/docker/android/check-workspace:/opt/android:rw",
            REMOTE_CHECK_SCRIPT,
        )
        self.assertIn("/mnt/fast/docker/android/ccache:/ccache:rw", REMOTE_CHECK_SCRIPT)
        self.assertIn(
            "/mnt/fast/docker/android/project:/opt/yrrp/project:ro",
            REMOTE_CHECK_SCRIPT,
        )
        self.assertNotIn(
            "/mnt/fast/docker/android/workspace:/opt/android",
            REMOTE_CHECK_SCRIPT,
        )

    def test_target_script_sources_environment_once_without_set_u(self) -> None:
        self.assertEqual(1, CHECK_TARGET_SCRIPT.count("source build/envsetup.sh"))
        self.assertEqual(1, CHECK_TARGET_SCRIPT.count("breakfast"))
        self.assertNotIn("set -u", CHECK_TARGET_SCRIPT)
        self.assertIn('run_one m "${compile_csv}"', CHECK_TARGET_SCRIPT)
        self.assertIn('atest "${atest_targets[${suite_index}]}"', CHECK_TARGET_SCRIPT)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_release.jobs import (
    JobProtocolError,
    local_paths_for_run,
    new_run_id,
    parse_run_id,
    paths_for_run,
    REMOTE_WAIT_SCRIPT,
    wait_for_job,
    write_result,
    write_status,
)

CHECK_RUN = "check-20261015-123456-a1b2c3d4"
ROLLBACK_RUN = "rollback-20261015-123456-deadbeef"


class RunIdentityTest(unittest.TestCase):
    def test_check_run_id_derives_only_check_root(self) -> None:
        run_id = new_run_id(
            "check",
            now=datetime(2026, 10, 15, 12, 34, 56, tzinfo=timezone.utc),
            random_hex="a1b2c3d4",
        )
        self.assertEqual(CHECK_RUN, run_id)
        self.assertEqual("check", parse_run_id(run_id))
        paths = paths_for_run(run_id)
        self.assertEqual(
            PurePosixPath(f"/opt/android/out/yrrp-checks/{CHECK_RUN}"),
            paths.directory,
        )
        self.assertEqual(paths.directory / "status.json", paths.status)
        self.assertEqual(paths.directory / "result.json", paths.result)
        self.assertEqual(paths.directory / "job.log", paths.log)

    def test_rollback_run_id_derives_only_rollback_root(self) -> None:
        paths = paths_for_run(ROLLBACK_RUN)
        self.assertEqual("rollback", parse_run_id(ROLLBACK_RUN))
        self.assertEqual(
            PurePosixPath(f"/opt/android/out/yrrp-rollbacks/{ROLLBACK_RUN}"),
            paths.directory,
        )

    def test_rejects_malformed_run_ids_and_suffixes(self) -> None:
        invalid = (
            "",
            "../check-20261015-123456-a1b2c3d4",
            "build-20261015-123456-a1b2c3d4",
            "check-20261015-123456-A1B2C3D4",
            "check-20261015-123456-a1b2c3d",
            "check-20261315-123456-a1b2c3d4",
            "check-20261015-123456-a1b2c3d4-extra",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_run_id(value)

    def test_rejects_invalid_generated_values(self) -> None:
        with self.assertRaises(ValueError):
            new_run_id("build")
        with self.assertRaises(ValueError):
            new_run_id("check", random_hex="A1B2C3D4")


class AtomicEvidenceTest(unittest.TestCase):
    def test_status_replaces_atomically_with_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = local_paths_for_run(CHECK_RUN, Path(directory))
            write_status(paths, {"phase": "running", "result": "pending"})
            self.assertEqual(
                {"phase": "running", "result": "pending"},
                json.loads(paths.status.read_text()),
            )
            self.assertEqual(0o600, stat.S_IMODE(paths.status.stat().st_mode))
            write_status(paths, {"phase": "complete", "result": "passed"})
            self.assertEqual("complete", json.loads(paths.status.read_text())["phase"])
            self.assertEqual([], list(paths.directory.glob("*.tmp")))

    def test_result_is_private_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = local_paths_for_run(CHECK_RUN, Path(directory))
            write_result(paths, {"result": "passed", "exit_code": 0})
            self.assertEqual(0o600, stat.S_IMODE(paths.result.stat().st_mode))
            with self.assertRaises(FileExistsError):
                write_result(paths, {"result": "changed", "exit_code": 0})


class NonClosingStringIO(io.StringIO):
    def close(self) -> None:
        self.flush()


class FakeProcess:
    def __init__(
        self, lines: list[str], returncode: int, stderr: str = ""
    ) -> None:
        self.stdin = NonClosingStringIO()
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class WaitProtocolTest(unittest.TestCase):
    def test_remote_wait_has_no_wall_clock_deadline_while_screen_lives(self) -> None:
        self.assertIn("while True:", REMOTE_WAIT_SCRIPT)
        self.assertNotIn("range(3000)", REMOTE_WAIT_SCRIPT)
        self.assertNotIn("timed out waiting", REMOTE_WAIT_SCRIPT)

    def test_emits_unique_phases_and_final_result(self) -> None:
        phase = {"run_id": CHECK_RUN, "phase": "source-sync", "result": "pending"}
        final = {
            "run_id": CHECK_RUN,
            "phase": "complete",
            "result": "passed",
            "exit_code": 0,
        }
        process = FakeProcess(
            [json.dumps(phase) + "\n", json.dumps(phase) + "\n", json.dumps(final) + "\n"],
            0,
        )
        emitted: list[dict] = []
        exit_code = wait_for_job(CHECK_RUN, emitted.append, popen=lambda *a, **k: process)
        self.assertEqual(0, exit_code)
        self.assertEqual([phase, final], emitted)
        self.assertEqual({"run_id": CHECK_RUN}, json.loads(process.stdin.getvalue()))

    def test_returns_failed_worker_exit_code(self) -> None:
        final = {
            "run_id": ROLLBACK_RUN,
            "phase": "failed",
            "result": "failed",
            "exit_code": 17,
        }
        process = FakeProcess([json.dumps(final) + "\n"], 17)
        emitted: list[dict] = []
        self.assertEqual(
            17,
            wait_for_job(ROLLBACK_RUN, emitted.append, popen=lambda *a, **k: process),
        )
        self.assertEqual([final], emitted)

    def test_rejects_malformed_or_incomplete_remote_events(self) -> None:
        malformed = FakeProcess(["not-json\n"], 2)
        with self.assertRaisesRegex(JobProtocolError, "invalid JSON"):
            wait_for_job(CHECK_RUN, lambda event: None, popen=lambda *a, **k: malformed)

        incomplete = FakeProcess(
            [json.dumps({"run_id": CHECK_RUN, "phase": "source-sync", "result": "pending"}) + "\n"],
            2,
            stderr="detached job exited without final result\n",
        )
        with self.assertRaises(JobProtocolError) as caught:
            wait_for_job(CHECK_RUN, lambda event: None, popen=lambda *a, **k: incomplete)
        message = str(caught.exception)
        self.assertIn("detached job exited without final result", message)
        self.assertIn(str(paths_for_run(CHECK_RUN).log), message)


if __name__ == "__main__":
    unittest.main()

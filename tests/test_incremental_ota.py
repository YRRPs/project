from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.make_release_fixture import (
    BUILD_ID,
    SOURCE_BUILD_ID,
    SOURCE_INCREMENTAL,
    create_fixture,
    create_incremental_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/incremental-ota.py"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from yrrp_ota.incremental import check_source, verify_output  # noqa: E402


class IncrementalOtaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _, self.source = create_fixture(
            self.root / "signed",
            build_id=SOURCE_BUILD_ID,
            target_timestamp=int(SOURCE_INCREMENTAL),
        )
        _, self.target = create_fixture(self.root / "signed")
        self.source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.release_json = self.root / "release.json"
        self.write_release(self.source_sha)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_release(self, digest: str) -> None:
        self.release_json.write_text(json.dumps({"schema": 1, "target_files": {"sha256": digest}}))

    def verify(self, incremental: Path, **overrides):
        arguments = {
            "source_build_id": SOURCE_BUILD_ID,
            "target_build_id": BUILD_ID,
            "source_incremental": SOURCE_INCREMENTAL,
            "source_target_files_sha256": self.source_sha,
        } | overrides
        return verify_output(incremental, self.target, **arguments)

    def test_check_source_returns_incremental_and_digest(self) -> None:
        result = check_source(self.source, self.release_json)
        self.assertEqual(
            {"source_incremental": SOURCE_INCREMENTAL, "source_target_files_sha256": self.source_sha},
            result,
        )

    def test_check_source_rejects_digest_mismatch(self) -> None:
        self.write_release("0" * 64)
        with self.assertRaisesRegex(ValueError, "does not match live release.json"):
            check_source(self.source, self.release_json)

    def test_check_source_rejects_release_without_digest(self) -> None:
        self.release_json.write_text(json.dumps({"schema": 1}))
        with self.assertRaisesRegex(ValueError, "no target-files SHA-256"):
            check_source(self.source, self.release_json)

    def test_check_source_rejects_non_numeric_incremental(self) -> None:
        _, source = create_fixture(
            self.root / "bad", build_id=SOURCE_BUILD_ID, target_incremental="eng.build"
        )
        self.write_release(hashlib.sha256(source.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "not numeric"):
            check_source(source, self.release_json)

    def test_verify_output_returns_meta(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed")
        meta = self.verify(incremental)
        self.assertEqual(incremental.name, meta["filename"])
        self.assertEqual(SOURCE_BUILD_ID, meta["source_build_id"])
        self.assertEqual(SOURCE_INCREMENTAL, meta["source_incremental"])
        self.assertEqual(incremental.stat().st_size, meta["size"])

    def test_verify_output_rejects_pre_build_mismatch(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed", pre_incremental="1")
        with self.assertRaisesRegex(ValueError, "pre-build-incremental does not match source build"):
            self.verify(incremental)

    def test_verify_output_rejects_post_timestamp_mismatch(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed", post_timestamp=1)
        with self.assertRaisesRegex(ValueError, "post-timestamp does not match target build"):
            self.verify(incremental)

    def test_verify_output_rejects_wrong_filename(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed", name="other.zip")
        with self.assertRaisesRegex(ValueError, "filename does not match"):
            self.verify(incremental)

    def test_cli_check_source_prints_fields(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "check-source", "--target-files", str(self.source),
             "--release-json", str(self.release_json)],
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"{SOURCE_INCREMENTAL} {self.source_sha}", result.stdout.strip())

    def test_cli_verify_output_writes_meta_and_reports_errors(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed")
        command = [
            sys.executable, str(CLI), "verify-output",
            "--incremental", str(incremental), "--target-files", str(self.target),
            "--source-build", SOURCE_BUILD_ID, "--target-build", BUILD_ID,
            "--source-incremental", SOURCE_INCREMENTAL, "--source-sha256", self.source_sha,
        ]
        ok = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, ok.returncode, ok.stderr)
        meta = json.loads(incremental.with_name(incremental.name + ".json").read_text())
        self.assertEqual(self.source_sha, meta["source_target_files_sha256"])
        bad = subprocess.run([*command[:-1], "0" * 63], capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertIn("incremental-ota:", bad.stderr)

    def test_gapps_channel_requires_gapps_incremental_name(self) -> None:
        from yrrp_ota.channel import GAPPS
        from yrrp_ota.incremental import check_identity

        sha = "a" * 64
        good = Path(f"/x/{GAPPS.incremental_ota_name('20261009-120000', '20261010-120000')}")
        check_identity(good, "20261009-120000", "20261010-120000", "7", sha, channel=GAPPS)
        vanilla_name = Path("/x/lineage-23.2-salami-20261009-120000-to-20261010-120000-signed-incremental-ota.zip")
        with self.assertRaises(ValueError):
            check_identity(vanilla_name, "20261009-120000", "20261010-120000", "7", sha, channel=GAPPS)

    def test_cli_verify_output_rejects_vanilla_zip_for_gapps_channel(self) -> None:
        incremental = create_incremental_fixture(self.root / "signed")
        result = subprocess.run(
            [
                sys.executable, str(CLI), "verify-output",
                "--incremental", str(incremental), "--target-files", str(self.target),
                "--source-build", SOURCE_BUILD_ID, "--target-build", BUILD_ID,
                "--source-incremental", SOURCE_INCREMENTAL, "--source-sha256", self.source_sha,
                "--channel", "salami/gapps",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()

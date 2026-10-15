from __future__ import annotations

import hashlib
import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_ota.channel import GAPPS
from yrrp_release.deployment_record import (
    build_deployment_record,
    prepare_deployment_record,
    publish_deployment_record,
    validate_deployment_record,
    verify_record_artifacts,
)

BUILD_ID = "20261015-123456"
SOURCE_BUILD = "20261014-123456"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeploymentRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ota = self.root / GAPPS.full_ota_name(BUILD_ID)
        self.target = self.root / GAPPS.target_files_name(BUILD_ID)
        self.checksums = self.root / GAPPS.checksums_name(BUILD_ID)
        self.ota.write_bytes(b"ota")
        self.target.write_bytes(b"target")
        self.write_checksums([self.ota, self.target])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_checksums(self, paths: list[Path]) -> None:
        self.checksums.write_text(
            "".join(f"{digest(path)}  {path.name}\n" for path in paths)
        )

    def build(self, incremental: Path | None = None) -> dict:
        return build_deployment_record(
            channel=GAPPS,
            build_id=BUILD_ID,
            signed_root=self.root,
            ota_name=self.ota.name,
            target_files_name=self.target.name,
            incremental_name=incremental.name if incremental else None,
            created_at=datetime(2026, 10, 15, 12, 50, tzinfo=timezone.utc),
            deployer_project_sha="a" * 40,
        )

    def test_builds_and_verifies_full_record(self) -> None:
        record = self.build()
        self.assertEqual(1, record["schema_version"])
        self.assertEqual("salami/gapps", record["channel"])
        self.assertEqual(BUILD_ID, record["build_id"])
        self.assertEqual(self.ota.name, record["full_ota"]["name"])
        self.assertEqual(digest(self.ota), record["full_ota"]["sha256"])
        self.assertIsNone(record["incremental"])
        paths = verify_record_artifacts(record, self.root)
        self.assertEqual(self.ota, paths["full_ota"])
        self.assertEqual(self.target, paths["target_files"])

    def test_builds_incremental_record_from_verified_metadata(self) -> None:
        incremental = self.root / GAPPS.incremental_ota_name(SOURCE_BUILD, BUILD_ID)
        incremental.write_bytes(b"incremental")
        Path(f"{incremental}.json").write_text(
            json.dumps({"source_incremental": "123456789"})
        )
        self.write_checksums([self.ota, self.target, incremental])
        record = self.build(incremental)
        self.assertEqual("123456789", record["incremental"]["source_incremental"])
        paths = verify_record_artifacts(record, self.root)
        self.assertEqual(incremental, paths["incremental"])
        self.assertEqual(Path(f"{incremental}.json"), paths["incremental_metadata"])
        Path(f"{incremental}.json").unlink()
        with self.assertRaises(ValueError):
            verify_record_artifacts(record, self.root)

    def test_rejects_modified_incremental_metadata(self) -> None:
        incremental = self.root / GAPPS.incremental_ota_name(SOURCE_BUILD, BUILD_ID)
        incremental.write_bytes(b"incremental")
        metadata = Path(f"{incremental}.json")
        metadata.write_text(json.dumps({"source_incremental": "123456789"}))
        self.write_checksums([self.ota, self.target, incremental])
        record = self.build(incremental)
        metadata.write_text(json.dumps({"source_incremental": "987654321"}))
        with self.assertRaises(ValueError):
            verify_record_artifacts(record, self.root)

    def test_rejects_wrong_names_digests_sizes_and_unknown_keys(self) -> None:
        mutations = []
        wrong_name = self.build()
        wrong_name["full_ota"]["name"] = "other.zip"
        mutations.append(wrong_name)
        wrong_digest = self.build()
        wrong_digest["target_files"]["sha256"] = "0" * 64
        mutations.append(wrong_digest)
        wrong_size = self.build()
        wrong_size["full_ota"]["size"] += 1
        mutations.append(wrong_size)
        unknown = self.build()
        unknown["extra"] = True
        mutations.append(unknown)
        for record in mutations:
            with self.subTest(record=record), self.assertRaises((TypeError, ValueError)):
                verify_record_artifacts(record, self.root)

    def test_rejects_symlinked_artifact(self) -> None:
        record = self.build()
        real = self.root / "real-ota"
        self.ota.rename(real)
        self.ota.symlink_to(real)
        with self.assertRaises(ValueError):
            verify_record_artifacts(record, self.root)

    def test_publishes_privately_without_overwriting_different_record(self) -> None:
        record = self.build()
        path = publish_deployment_record(record, self.root)
        self.assertEqual(
            self.root / "deployments/salami/gapps/20261015-123456.json",
            path,
        )
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(path, publish_deployment_record(record, self.root))
        changed = json.loads(json.dumps(record))
        changed["created_at"] = "2026-10-15T12:51:00Z"
        with self.assertRaises(FileExistsError):
            publish_deployment_record(changed, self.root)

    def test_prepare_reuses_existing_record_bytes(self) -> None:
        original = self.build()
        path = publish_deployment_record(original, self.root)
        prepared = prepare_deployment_record(
            channel=GAPPS,
            build_id=BUILD_ID,
            signed_root=self.root,
            ota_name=self.ota.name,
            target_files_name=self.target.name,
            incremental_name=None,
            created_at=datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc),
            deployer_project_sha="f" * 40,
        )
        self.assertEqual(original, prepared)
        self.assertEqual(json.loads(path.read_text()), prepared)

    def test_validation_returns_copy(self) -> None:
        record = self.build()
        validated = validate_deployment_record(
            record, expected_channel=GAPPS, expected_build_id=BUILD_ID
        )
        self.assertEqual(record, validated)
        self.assertIsNot(record, validated)


if __name__ == "__main__":
    unittest.main()

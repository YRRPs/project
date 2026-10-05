from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.make_release_fixture import BUILD_ID, IMAGES, OTA_NAME, create_fixture

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/prepare-ota-release.py"


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_ota_release", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load release preparer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PrepareOtaReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ota, self.target = create_fixture(self.root / "input")
        self.module = load_module()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prepare(self, output_name: str = "release", **overrides):
        arguments = {
            "ota": self.ota,
            "target_files": self.target,
            "build_id": BUILD_ID,
            "public_base_url": "https://ota.example.invalid/",
            "base_image_digest": "ghcr.io/yrrp/ota@sha256:" + "a" * 64,
            "output": self.root / output_name,
        }
        arguments.update(overrides)
        return self.module.prepare_release(**arguments)

    def test_generates_exact_release_tree_and_updater_contract(self) -> None:
        output = self.prepare()
        release_dir = output / "rootfs/install/salami" / BUILD_ID
        expected = {
            Path("rootfs/updates/salami.json"),
            *(Path("rootfs/install/salami") / BUILD_ID / name for name in (OTA_NAME, *IMAGES, "SHA256SUMS.txt", "release.json")),
        }
        actual = {path.relative_to(output) for path in output.rglob("*") if path.is_file()}
        self.assertEqual(expected, actual)

        updates = json.loads((output / "rootfs/updates/salami.json").read_text())
        self.assertIsInstance(updates, list)
        self.assertEqual(1, len(updates))
        update = updates[0]
        self.assertEqual(4070908800, update["datetime"])
        self.assertEqual("UNOFFICIAL", update["type"])
        self.assertEqual("23.2", update["version"])
        self.assertEqual(OTA_NAME, update["files"][0]["filename"])
        self.assertEqual(
            f"https://ota.example.invalid/install/salami/{BUILD_ID}/{OTA_NAME}",
            update["files"][0]["url"],
        )
        copied_ota = release_dir / OTA_NAME
        self.assertEqual(copied_ota.stat().st_size, update["files"][0]["size"])
        self.assertEqual(hashlib.sha256(copied_ota.read_bytes()).hexdigest(), update["files"][0]["sha256"])
        self.assertFalse(any(path.name == self.target.name for path in output.rglob("*")))

    def test_rejects_non_https_public_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.prepare(public_base_url="http://ota.example.invalid")

    def test_rejects_non_release_key_ota(self) -> None:
        self.ota, self.target = create_fixture(self.root / "test-keys", post_build_suffix="test-keys")
        with self.assertRaisesRegex(ValueError, "release-keys"):
            self.prepare()

    def test_missing_required_image_does_not_fall_back_to_prebuilt(self) -> None:
        self.ota, self.target = create_fixture(self.root / "missing", missing_image="dtbo.img")
        with self.assertRaisesRegex(ValueError, "IMAGES/dtbo.img"):
            self.prepare()

    def test_rejects_duplicate_zip_members(self) -> None:
        self.ota, self.target = create_fixture(self.root / "duplicate", duplicate_member=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.prepare()

    def test_rejects_target_files_from_different_build(self) -> None:
        self.ota, self.target = create_fixture(
            self.root / "mismatch",
            target_fingerprint="yrrp/salami/salami:16/OTHER/1:userdebug/release-keys",
        )
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.prepare()

    def test_rejects_target_files_from_different_timestamp(self) -> None:
        self.ota, self.target = create_fixture(self.root / "timestamp", target_timestamp=4070908700)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            self.prepare()

    def test_accepts_legacy_system_build_prop_location(self) -> None:
        self.ota, self.target = create_fixture(
            self.root / "legacy-system-prop",
            system_prop_path="SYSTEM/build.prop",
        )
        self.assertTrue(self.prepare().is_dir())

    def test_metadata_outputs_are_deterministic(self) -> None:
        first = self.prepare("first")
        second = self.prepare("second")
        for relative in (
            Path("rootfs/updates/salami.json"),
            Path("rootfs/install/salami") / BUILD_ID / "release.json",
            Path("rootfs/install/salami") / BUILD_ID / "SHA256SUMS.txt",
        ):
            self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()

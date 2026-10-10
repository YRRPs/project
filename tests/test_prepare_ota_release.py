from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.make_release_fixture import (
    BUILD_ID,
    IMAGES,
    INCREMENTAL_NAME,
    OTA_NAME,
    SOURCE_BUILD_ID,
    SOURCE_INCREMENTAL,
    create_fixture,
    create_incremental_fixture,
    write_incremental_meta,
)

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


    def make_incremental(self, **overrides) -> Path:
        meta_overrides = {k: overrides.pop(k) for k in ("source_incremental", "digest") if k in overrides}
        incremental = create_incremental_fixture(self.root / "input", **overrides)
        write_incremental_meta(incremental, **meta_overrides)
        return incremental

    def test_full_release_records_schema_three_without_incremental(self) -> None:
        output = self.prepare()
        release = json.loads((output / "rootfs/install/salami" / BUILD_ID / "release.json").read_text())
        self.assertEqual(3, release["schema"])
        self.assertIsNone(release["incremental"])

    def test_incremental_release_tree_and_updater_contract(self) -> None:
        output = self.prepare(incremental=self.make_incremental())
        install = Path("rootfs/install/salami") / BUILD_ID
        expected = {
            Path("rootfs/updates/salami.json"),
            Path(f"rootfs/updates/salami/{SOURCE_INCREMENTAL}.json"),
            *(install / name for name in (OTA_NAME, INCREMENTAL_NAME, *IMAGES, "SHA256SUMS.txt", "release.json")),
        }
        actual = {path.relative_to(output) for path in output.rglob("*") if path.is_file()}
        self.assertEqual(expected, actual)

        full = json.loads((output / "rootfs/updates/salami.json").read_text())
        self.assertEqual(OTA_NAME, full[0]["files"][0]["filename"])
        entry = json.loads((output / f"rootfs/updates/salami/{SOURCE_INCREMENTAL}.json").read_text())
        self.assertEqual(1, len(entry))
        file = entry[0]["files"][0]
        self.assertEqual(INCREMENTAL_NAME, file["filename"])
        self.assertEqual(f"https://ota.example.invalid/install/salami/{BUILD_ID}/{INCREMENTAL_NAME}", file["url"])
        self.assertEqual("payload.bin:5:6,metadata:7:8", file["ota_property_files"])
        self.assertEqual(4070908800, entry[0]["datetime"])

        release = json.loads((output / install / "release.json").read_text())
        self.assertEqual(SOURCE_INCREMENTAL, release["incremental"]["source_incremental"])
        self.assertEqual(INCREMENTAL_NAME, release["incremental"]["filename"])
        self.assertIn(INCREMENTAL_NAME, (output / install / "SHA256SUMS.txt").read_text())

    def test_rejects_incremental_pre_build_mismatch(self) -> None:
        incremental = self.make_incremental(pre_incremental="1")
        with self.assertRaisesRegex(ValueError, "pre-build-incremental"):
            self.prepare(incremental=incremental)

    def test_rejects_incremental_for_other_post_build(self) -> None:
        incremental = self.make_incremental(post_build="other/release-keys")
        with self.assertRaisesRegex(ValueError, "post-build does not match full OTA"):
            self.prepare(incremental=incremental)

    def test_rejects_incremental_meta_digest_mismatch(self) -> None:
        incremental = self.make_incremental(digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256 or size does not match meta"):
            self.prepare(incremental=incremental)

    def test_rejects_incremental_for_other_target_build(self) -> None:
        incremental = self.make_incremental(
            name="lineage-23.2-salami-20981231-000000-to-20990102-000000-signed-incremental-ota.zip"
        )
        with self.assertRaisesRegex(ValueError, "filename does not match"):
            self.prepare(incremental=incremental)

    def test_rejects_non_numeric_meta_incremental(self) -> None:
        incremental = self.make_incremental(source_incremental="../x")
        with self.assertRaisesRegex(ValueError, "numeric"):
            self.prepare(incremental=incremental)

    def ota_named_for(self, channel) -> Path:
        renamed = self.ota.with_name(channel.full_ota_name(BUILD_ID))
        shutil.copyfile(self.ota, renamed)
        return renamed

    def test_gapps_channel_writes_gapps_tree_and_schema_3(self) -> None:
        from yrrp_ota.channel import GAPPS

        ota, target = create_fixture(self.root / "gapps-input", build_type="gapps")
        output = self.prepare("gapps-out", ota=ota, target_files=target, channel=GAPPS)
        rootfs = output / "rootfs"
        install = Path("rootfs/install/salami/gapps") / BUILD_ID
        expected = {
            Path("rootfs/updates/salami/gapps.json"),
            *(install / name for name in (GAPPS.full_ota_name(BUILD_ID), *IMAGES, "SHA256SUMS.txt", "release.json")),
        }
        self.assertEqual(expected, {path.relative_to(output) for path in output.rglob("*") if path.is_file()})
        self.assertFalse((rootfs / "updates/salami.json").exists())
        release = json.loads((output / install / "release.json").read_text())
        self.assertEqual(3, release["schema"])
        self.assertEqual("salami/gapps", release["channel"])
        self.assertEqual("salami", release["device"])
        entry = json.loads((rootfs / "updates/salami/gapps.json").read_text())
        self.assertIn(f"/install/salami/gapps/{BUILD_ID}/", entry[0]["files"][0]["url"])

    def test_gapps_incremental_lands_under_gapps_updates(self) -> None:
        from yrrp_ota.channel import GAPPS

        ota, target = create_fixture(self.root / "gapps-input", build_type="gapps")
        name = GAPPS.incremental_ota_name(SOURCE_BUILD_ID, BUILD_ID)
        incremental = create_incremental_fixture(self.root / "gapps-input", name=name)
        write_incremental_meta(incremental)
        output = self.prepare("gapps-out", ota=ota, target_files=target, channel=GAPPS, incremental=incremental)
        entry = json.loads((output / f"rootfs/updates/salami/gapps/{SOURCE_INCREMENTAL}.json").read_text())
        self.assertEqual(name, entry[0]["files"][0]["filename"])
        self.assertIn(f"/install/salami/gapps/{BUILD_ID}/{name}", entry[0]["files"][0]["url"])
        self.assertFalse((output / f"rootfs/updates/salami/{SOURCE_INCREMENTAL}.json").exists())

    def test_build_type_must_match_channel(self) -> None:
        from yrrp_ota.channel import GAPPS

        with self.assertRaisesRegex(ValueError, "ro.yrrp.build.type"):
            self.prepare("mismatch", ota=self.ota_named_for(GAPPS), channel=GAPPS)

    def test_vanilla_channel_rejects_gapps_target_files(self) -> None:
        _, gapps_target = create_fixture(self.root / "gapps-input", build_type="gapps")
        with self.assertRaisesRegex(ValueError, "ro.yrrp.build.type"):
            self.prepare("mismatch", target_files=gapps_target)

    def test_vanilla_channel_accepts_explicit_vanilla_build_type(self) -> None:
        ota, target = create_fixture(self.root / "typed-input", build_type="vanilla")
        output = self.prepare("typed-out", ota=ota, target_files=target)
        self.assertTrue((output / "rootfs/updates/salami.json").is_file())

    def test_vanilla_channel_keeps_todays_tree(self) -> None:
        output = self.prepare("vanilla-out")
        release = json.loads((output / f"rootfs/install/salami/{BUILD_ID}/release.json").read_text())
        self.assertEqual("salami/vanilla", release["channel"])
        self.assertTrue((output / "rootfs/updates/salami.json").is_file())


if __name__ == "__main__":
    unittest.main()

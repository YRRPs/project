from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.make_release_fixture import (
    BUILD_ID,
    INCREMENTAL_NAME,
    POST_BUILD,
    SOURCE_BUILD_ID,
    SOURCE_INCREMENTAL,
    create_fixture,
)
from tests.fixtures.release_commands import write_release_command_stubs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate-incremental-ota.sh"
FIXTURES = ROOT / "tests/fixtures"
WARNING = (
    "Builds doesn't support zucchini, or source/target don't have compatible "
    "zucchini versions. Disabling zucchini."
)


class GenerateIncrementalOtaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.build = self.root / "android"
        self.signed = self.build / "out/signed"
        self.cert = self.root / "certs"
        self.bin = self.root / "bin"
        _, self.source = create_fixture(
            self.signed, build_id=SOURCE_BUILD_ID, target_timestamp=int(SOURCE_INCREMENTAL)
        )
        _, self.target = create_fixture(self.signed)
        self.release_json = self.root / "release.json"
        self.write_release_json(hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.cert.mkdir()
        (self.cert / "passwords").write_text(
            "[[[ synthetic-password ]]] /home/android/.android-certs/releasekey\n"
        )
        (self.cert / "releasekey.pk8").write_bytes(b"key")
        (self.cert / "releasekey.x509.pem").write_bytes(b"certificate")
        write_release_command_stubs(self.bin)
        shutil.copy2(FIXTURES / "fake_live_docker.py", self.bin / "docker")
        (self.bin / "docker").chmod(0o755)
        tool = self.build / "out/host/linux-x86/bin/ota_from_target_files"
        tool.parent.mkdir(parents=True)
        shutil.copy2(FIXTURES / "fake_ota_from_target_files.py", tool)
        tool.chmod(0o755)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_release_json(self, digest: str) -> None:
        self.release_json.write_text(json.dumps({"schema": 1, "target_files": {"sha256": digest}}))

    def stage_channel_target_files(self, build_type: str) -> None:
        _, source = create_fixture(
            self.signed, build_id=SOURCE_BUILD_ID, target_timestamp=int(SOURCE_INCREMENTAL), build_type=build_type
        )
        self.write_release_json(hashlib.sha256(source.read_bytes()).hexdigest())
        create_fixture(self.signed, build_type=build_type)

    def run_script(
        self, channel: str | None = None, env_extra: dict[str, str] | None = None, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        inherited = {k: v for k, v in os.environ.items() if k not in {"ANDROID_PW_FILE", "YRRP_BUILD_LOCK_HELD"}}
        env = inherited | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "YRRP_BUILD_ROOT": str(self.build),
            "YRRP_CERT_DIR": str(self.cert),
            "YRRP_BUILD_LOCK_FILE": str(self.root / "build.lock"),
            "YRRP_INCREMENTAL_PASSWORD_FILE": str(self.root / "runtime-passwords"),
            "FAKE_LIVE_BUILD": SOURCE_BUILD_ID,
            "FAKE_RELEASE_JSON": str(self.release_json),
            "FAKE_OTA_ARGS": str(self.root / "ota-args.json"),
            "FAKE_OTA_PASSWORDS": str(self.root / "ota-passwords.txt"),
            "FAKE_POST_BUILD": POST_BUILD,
            "FAKE_POST_TIMESTAMP": "4070908800",
            "FAKE_PRE_INCREMENTAL": SOURCE_INCREMENTAL,
        } | overrides | (env_extra or {})
        channel_args = ["--channel", channel] if channel else []
        return subprocess.run(
            [str(SCRIPT), "--source-build", SOURCE_BUILD_ID, "--target-build", BUILD_ID, *channel_args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_generates_verified_incremental_with_both_delta_tools(self) -> None:
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        args = json.loads((self.root / "ota-args.json").read_text())
        self.assertEqual(
            [
                "-k", str(self.cert / "releasekey"), "--block",
                "-i", str(self.source),
                "--enable_zucchini=true", "--enable_lz4diff=true",
                str(self.target), str(self.signed / INCREMENTAL_NAME),
            ],
            args,
        )
        meta = json.loads((self.signed / f"{INCREMENTAL_NAME}.json").read_text())
        self.assertEqual(SOURCE_BUILD_ID, meta["source_build_id"])
        self.assertEqual(SOURCE_INCREMENTAL, meta["source_incremental"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), meta["source_target_files_sha256"])
        self.assertIn(str(self.cert / "releasekey"), (self.root / "ota-passwords.txt").read_text())
        self.assertFalse((self.root / "runtime-passwords").exists())

    def test_disabled_delta_tool_fails(self) -> None:
        result = self.run_script(FAKE_OTA_WARNINGS=WARNING)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Disabling zucchini", result.stderr)
        self.assertIn("lost a delta optimization", result.stderr)

    def test_source_digest_mismatch_fails_before_generation(self) -> None:
        self.write_release_json("0" * 64)
        result = self.run_script()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match live release.json", result.stderr)
        self.assertFalse((self.root / "ota-args.json").exists())

    def test_source_must_be_live_release(self) -> None:
        result = self.run_script(FAKE_LIVE_BUILD="20981230-000000")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("is not the live salami/vanilla release", result.stderr)

    def test_pre_build_mismatch_fails(self) -> None:
        result = self.run_script(FAKE_PRE_INCREMENTAL="1")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pre-build-incremental does not match source build", result.stderr)

    def test_refuses_while_release_build_holds_lock(self) -> None:
        with (self.root / "build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_script()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("A release build holds", result.stderr)

    def test_inherited_password_file_is_reused_and_kept(self) -> None:
        inherited = self.root / "inherited-passwords"
        inherited.write_text("[[[ inherited ]]] releasekey\n")
        result = self.run_script(ANDROID_PW_FILE=str(inherited), YRRP_BUILD_LOCK_HELD="1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("inherited", (self.root / "ota-passwords.txt").read_text())
        self.assertTrue(inherited.exists())

    def test_gapps_channel_uses_gapps_label_and_install_path(self) -> None:
        labels = {
            "io.yrrp.ota.channel.salami.gapps.build-id": SOURCE_BUILD_ID,
            "io.yrrp.ota.channel.salami.vanilla.build-id": "20200101-000000",
        }
        exec_log = self.root / "exec.log"
        self.stage_channel_target_files("gapps")
        result = self.run_script(
            channel="salami/gapps",
            env_extra={"FAKE_LIVE_LABELS": json.dumps(labels), "FAKE_EXEC_LOG": str(exec_log)},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"/srv/ota/install/salami/gapps/{SOURCE_BUILD_ID}/release.json", exec_log.read_text())
        name = f"lineage-23.2-salami-gapps-{SOURCE_BUILD_ID}-to-{BUILD_ID}-signed-incremental-ota.zip"
        self.assertTrue((self.signed / name).exists())

    def test_rejects_source_that_is_not_the_channels_live_build(self) -> None:
        labels = {
            "io.yrrp.ota.channel.salami.gapps.build-id": "20200101-000000",
            "io.yrrp.ota.channel.salami.vanilla.build-id": SOURCE_BUILD_ID,
        }
        self.stage_channel_target_files("gapps")
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_LIVE_LABELS": json.dumps(labels)})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("is not the live salami/gapps release", result.stderr)

    def test_channel_without_live_build_reports_none(self) -> None:
        labels = {"io.yrrp.ota.channel.salami.vanilla.build-id": SOURCE_BUILD_ID}
        self.stage_channel_target_files("gapps")
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_LIVE_LABELS": json.dumps(labels)})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("is not the live salami/gapps release (none)", result.stderr)

    def test_unlabeled_live_container_reports_none(self) -> None:
        result = self.run_script(FAKE_LIVE_BUILD="")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("is not the live salami/vanilla release (none)", result.stderr)

    def test_unreadable_live_labels_fail(self) -> None:
        result = self.run_script(env_extra={"FAKE_LIVE_LABELS": "not json"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot read live channel labels", result.stderr)

    def test_rejects_unknown_channel(self) -> None:
        result = self.run_script(channel="salami/nonsense")
        self.assertEqual(64, result.returncode)

    def test_rejects_invalid_build_ids(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--source-build", "latest", "--target-build", BUILD_ID],
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)


if __name__ == "__main__":
    unittest.main()

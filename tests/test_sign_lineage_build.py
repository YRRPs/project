from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.fixtures.make_release_fixture import SOURCE_BUILD_ID
from tests.fixtures.release_commands import write_executable, write_release_command_stubs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sign-lineage-build.sh"
BUILD_ID = "20990101-000000"
INCREMENTAL = f"lineage-23.2-salami-{SOURCE_BUILD_ID}-to-{BUILD_ID}-signed-incremental-ota.zip"
FAKE_LIVE_DOCKER = ROOT / "tests/fixtures/fake_live_docker.py"
CHANNEL_TOOL = ROOT / "scripts/ota-channel.py"


class SignLineageBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.build = self.root / "android"
        self.cert = self.root / "certs"
        self.bin = self.root / "bin"
        self.status = self.root / "status"
        self.deploy_log = self.root / "deploy.log"
        self.build_log = self.root / "build.log"
        self.password_log = self.root / "password.log"
        self.incremental_log = self.root / "incremental.log"
        self.profile_log = self.root / "profile.log"
        self.build_type_log = self.root / "build-type.log"
        self.docker_log = self.root / "docker.log"
        self.signed = self.build / "out/signed"
        self._create_build_tree()
        self._create_keys()
        self._create_commands()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_build_tree(self) -> None:
        target_dir = self.build / "out/target/product/salami/obj/PACKAGING/target_files_intermediates"
        target_dir.mkdir(parents=True)
        self.unsigned_target = target_dir / "synthetic-target_files.zip"
        with zipfile.ZipFile(self.unsigned_target, "w") as archive:
            archive.writestr("SYSTEM_EXT/priv-app/SystemUI/SystemUI.apk", b"synthetic-systemui")
        self.fake_ota = self.root / "signed-ota-source.zip"
        with zipfile.ZipFile(self.fake_ota, "w") as archive:
            archive.writestr("META-INF/com/android/otacert", b"synthetic-certificate")
        (self.build / "build").mkdir()
        (self.build / "build/envsetup.sh").write_text(
            f"OUT={self.build}/out/target/product/salami\n"
            "breakfast() { printf '%s\\n' \"$YRRP_BUILD_TYPE\" > \"${FAKE_BUILD_TYPE_LOG}\"; }\n"
            "mka() { printf '%s\\n' \"$*\" > \"${YRRP_BUILD_LOG}\"; }\n"
            "sign_target_files_apks() { cp \"${ANDROID_PW_FILE}\" \"${YRRP_PASSWORD_LOG}\"; "
            "printf '%s' \"${YRRP_SIGNING_PROFILE_DIR:-unset}\" > \"${YRRP_PROFILE_LOG}\"; "
            "cp \"${@: -2:1}\" \"${@: -1}\"; }\n"
            "ota_from_target_files() { cp \"${FAKE_OTA_SOURCE}\" \"${@: -1}\"; }\n"
        )
        apksigner = self.build / "out/host/linux-x86/bin/apksigner"
        apksigner.parent.mkdir(parents=True)
        digest = hashlib.sha256(b"synthetic-der").hexdigest()
        apksigner.write_text(f"#!/bin/sh\necho 'Signer #1 certificate SHA-256 digest: {digest}'\n")
        apksigner.chmod(0o755)
        (self.build / "prebuilts/jdk/jdk21/linux-x86/bin").mkdir(parents=True)

    def _create_keys(self) -> None:
        self.cert.mkdir()
        (self.cert / "passwords").write_text(
            "[[[ synthetic-password ]]] /home/android/.android-certs/releasekey\n"
        )
        for name in ("releasekey", "platform"):
            (self.cert / f"{name}.pk8").write_bytes(b"key")
            (self.cert / f"{name}.x509.pem").write_bytes(b"certificate")
        for index in range(75):
            stem = f"apex-{index:02d}"
            (self.cert / f"{stem}.pk8").write_bytes(b"key")
            (self.cert / f"{stem}.x509.pem").write_bytes(b"certificate")
            (self.cert / f"{stem}.pem").write_bytes(b"payload")

    def _create_commands(self) -> None:
        self.bin.mkdir()
        write_release_command_stubs(self.bin)
        shutil.copy2(FAKE_LIVE_DOCKER, self.bin / "docker")
        (self.bin / "docker").chmod(0o755)
        write_executable(
            self.bin / "stat",
            "#!/bin/sh\n"
            "last=\n"
            "for argument in \"$@\"; do last=$argument; done\n"
            "if [ \"$last\" = /home/android/.yrrp-build-launch.lock ] "
            "&& [ -n \"${YRRP_FAKE_CANONICAL_LOCK_ID:-}\" ]; then\n"
            "  printf '%s\\n' \"$YRRP_FAKE_CANONICAL_LOCK_ID\"\n"
            "  exit 0\n"
            "fi\n"
            "exec /usr/bin/stat \"$@\"\n",
        )

    def _create_live_source(self, content: bytes = b"source") -> None:
        signed = self.build / "out/signed"
        signed.mkdir(parents=True, exist_ok=True)
        (signed / f"lineage-23.2-salami-{SOURCE_BUILD_ID}-signed-target_files.zip").write_bytes(content)

    def _incremental_stub(self, exit_code: int, unreadable_output: bool = False) -> Path:
        # Expects: --channel C --source-build S --target-build T, and names the output like the real script.
        lock_output = f"chmod 000 \"{self.signed}/$name\"\n" if unreadable_output else ""
        return write_executable(
            self.root / "generate-incremental.sh",
            "#!/bin/sh\n"
            f"printf '%s %s\\n' \"$YRRP_BUILD_LOCK_HELD\" \"$*\" > {self.incremental_log}\n"
            f"[ {exit_code} -eq 0 ] || exit {exit_code}\n"
            f"name=$(python3 {CHANNEL_TOOL} --channel \"$2\" incremental \"$4\" \"$6\") || exit 99\n"
            f"printf incremental > \"{self.signed}/$name\"\n"
            + lock_output,
        )

    def run_script(
        self,
        deploy_exit: int = 0,
        *,
        include_ota_environment: bool = True,
        lock_mode: str = "locked",
        live_build: str | None = None,
        live_device: str = "salami",
        incremental_exit: int = 0,
        unreadable_incremental: bool = False,
        inspect_fail: str = "",
        channel: str | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        deploy = self.root / "deploy.sh"
        deploy.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" > {self.deploy_log}\n"
            f"exit {deploy_exit}\n"
        )
        deploy.chmod(0o755)
        env = os.environ | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "YRRP_BUILD_ROOT": str(self.build),
            "YRRP_CERT_DIR": str(self.cert),
            "YRRP_STATUS_FILE": str(self.status),
            "YRRP_DEPLOY_SCRIPT": str(deploy),
            "YRRP_BUILD_DATE": BUILD_ID,
            "FAKE_OTA_SOURCE": str(self.fake_ota),
            "YRRP_BUILD_LOG": str(self.build_log),
            "YRRP_PASSWORD_LOG": str(self.password_log),
            "YRRP_PROFILE_LOG": str(self.profile_log),
            "YRRP_RUNTIME_PASSWORD_FILE": str(self.root / "runtime-passwords"),
            "OTA_PUBLIC_BASE_URL": "https://ota.example.invalid",
            "OTA_BASE_IMAGE_REF": "ghcr.io/yrrp/ota:main",
            "YRRP_BUILD_LOCK_HELD": "1",
            "YRRP_INCREMENTAL_SCRIPT": str(
                self._incremental_stub(incremental_exit, unreadable_incremental)
            ),
            "FAKE_LIVE_BUILD": live_build or "",
            "FAKE_LIVE_DEVICE": live_device,
            "FAKE_INSPECT_FAIL": inspect_fail,
            "FAKE_BUILD_TYPE_LOG": str(self.build_type_log),
            "FAKE_DOCKER_CALL_LOG": str(self.docker_log),
        } | (env_extra or {})
        if not include_ota_environment:
            env.pop("OTA_PUBLIC_BASE_URL")
            env.pop("OTA_BASE_IMAGE_REF")
        handles = []
        pass_fds = ()
        try:
            if lock_mode == "invalid":
                env["YRRP_BUILD_LOCK_FD"] = "not-a-fd"
            elif lock_mode != "missing":
                canonical_path = self.root / "launch.lock"
                candidate_path = (
                    self.root / "unrelated.lock"
                    if lock_mode == "unrelated"
                    else canonical_path
                )
                canonical_path.touch()
                candidate = candidate_path.open("a+")
                handles.append(candidate)
                if lock_mode in {"locked", "unrelated"}:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif lock_mode == "contended":
                    holder = canonical_path.open("a+")
                    handles.append(holder)
                    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise ValueError(f"unknown lock mode: {lock_mode}")
                canonical_stat = canonical_path.stat()
                env["YRRP_FAKE_CANONICAL_LOCK_ID"] = (
                    f"{canonical_stat.st_dev}:{canonical_stat.st_ino}"
                )
                env["YRRP_BUILD_LOCK_FD"] = str(candidate.fileno())
                pass_fds = (candidate.fileno(),)
            channel_args = ["--channel", channel] if channel else []
            return subprocess.run(
                [str(SCRIPT), *channel_args],
                capture_output=True,
                text=True,
                env=env,
                pass_fds=pass_fds,
            )
        finally:
            for handle in handles:
                handle.close()

    def test_signing_runs_with_profile_directory_for_build(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.build / "out/signed/profile" / f"vanilla-{BUILD_ID}"
        self.assertEqual(self.profile_log.read_text(), str(expected))
        self.assertIn(f"Signing profile: {expected}", result.stdout)

    def test_fake_lock_marker_without_inherited_fd_cannot_start(self) -> None:
        result = self.run_script(lock_mode="missing")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("YRRP_BUILD_LOCK_FD", result.stderr)
        self.assertFalse(self.build_log.exists())

    def test_invalid_inherited_fd_is_rejected(self) -> None:
        result = self.run_script(lock_mode="invalid")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("YRRP_BUILD_LOCK_FD", result.stderr)
        self.assertFalse(self.build_log.exists())

    def test_inherited_locked_fd_allows_existing_flow(self) -> None:
        result = self.run_script(lock_mode="locked")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("target-files-package otatools", self.build_log.read_text().strip())

    def test_locked_unrelated_fd_is_rejected(self) -> None:
        result = self.run_script(lock_mode="unrelated")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("canonical release lock", result.stderr)
        self.assertFalse(self.build_log.exists())

    def test_unlocked_inherited_fd_fails_when_lock_is_contended(self) -> None:
        result = self.run_script(lock_mode="contended")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not hold the release lock", result.stderr)
        self.assertFalse(self.build_log.exists())

    def test_uses_yrrp_ota_defaults(self) -> None:
        result = self.run_script(include_ota_environment=False)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_success_deploys_exact_new_artifacts_before_complete(self) -> None:
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("complete", self.status.read_text().strip())
        self.assertEqual("target-files-package otatools", self.build_log.read_text().strip())
        password_map = self.password_log.read_text()
        self.assertIn(str(self.cert / "releasekey"), password_map)
        self.assertNotIn("/home/android/.android-certs", password_map)
        invocation = self.deploy_log.read_text()
        self.assertIn(f"--build-id {BUILD_ID}", invocation)
        self.assertIn(f"lineage-23.2-salami-{BUILD_ID}-signed-ota.zip", invocation)
        self.assertIn(f"lineage-23.2-salami-{BUILD_ID}-signed-target_files.zip", invocation)

    def test_deployment_failure_has_distinct_status_and_preserves_outputs(self) -> None:
        result = self.run_script(deploy_exit=23)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("deployment-failed:23", self.status.read_text().strip())
        signed = self.build / "out/signed"
        self.assertTrue((signed / f"lineage-23.2-salami-{BUILD_ID}-signed-ota.zip").is_file())
        self.assertTrue((signed / f"lineage-23.2-salami-{BUILD_ID}-signed-target_files.zip").is_file())

    def test_live_release_source_generates_and_deploys_incremental(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"1 --channel salami/vanilla --source-build {SOURCE_BUILD_ID} --target-build {BUILD_ID}",
            self.incremental_log.read_text().strip(),
        )
        signed = self.build / "out/signed"
        self.assertIn(f"--incremental {signed}/{INCREMENTAL}", self.deploy_log.read_text())
        sums = (signed / f"lineage-23.2-salami-{BUILD_ID}-SHA256SUMS.txt").read_text()
        self.assertIn(INCREMENTAL, sums)

    def test_without_live_container_deploys_full_only(self) -> None:
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("incremental-skipped: no live OTA container", result.stderr)
        self.assertNotIn("--incremental", self.deploy_log.read_text())
        self.assertFalse(self.incremental_log.exists())

    def test_live_build_without_signed_target_files_deploys_full_only(self) -> None:
        result = self.run_script(live_build=SOURCE_BUILD_ID)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"incremental-skipped: no signed target-files for live build {SOURCE_BUILD_ID}", result.stderr)
        self.assertNotIn("--incremental", self.deploy_log.read_text())

    def test_other_device_container_fails_incremental_without_deploy(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID, live_device="other")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("unsupported device", result.stderr)
        self.assertTrue(self.status.read_text().startswith("incremental-failed:"))
        self.assertFalse(self.deploy_log.exists())

    def test_incremental_failure_has_distinct_status_and_skips_deploy(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID, incremental_exit=31)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("incremental-failed:31", self.status.read_text().strip())
        self.assertFalse(self.deploy_log.exists())
        signed = self.build / "out/signed"
        self.assertTrue((signed / f"lineage-23.2-salami-{BUILD_ID}-signed-ota.zip").is_file())

    def test_empty_live_source_still_runs_incremental_generation(self) -> None:
        self._create_live_source(content=b"")
        result = self.run_script(live_build=SOURCE_BUILD_ID, incremental_exit=41)
        self.assertNotIn("incremental-skipped", result.stderr)
        self.assertEqual(
            f"1 --channel salami/vanilla --source-build {SOURCE_BUILD_ID} --target-build {BUILD_ID}",
            self.incremental_log.read_text().strip(),
        )
        self.assertEqual("incremental-failed:41", self.status.read_text().strip())
        self.assertFalse(self.deploy_log.exists())

    def test_failing_label_inspect_fails_incremental_without_deploy(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID, inspect_fail="{{json .Config.Labels}}")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("incremental-skipped", result.stderr)
        self.assertTrue(self.status.read_text().startswith("incremental-failed:"))
        self.assertFalse(self.deploy_log.exists())
        self.assertFalse(self.incremental_log.exists())

    def test_malformed_live_labels_fail_incremental_without_deploy(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID, env_extra={"FAKE_LIVE_LABELS": "not json"})
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("incremental-skipped", result.stderr)
        self.assertTrue(self.status.read_text().startswith("incremental-failed:"))
        self.assertFalse(self.deploy_log.exists())
        self.assertFalse(self.incremental_log.exists())

    def test_live_container_without_channel_release_deploys_full_only(self) -> None:
        labels = {"io.yrrp.ota.channel.salami.vanilla.build-id": SOURCE_BUILD_ID}
        result = self.run_script(
            channel="salami/gapps", live_build=SOURCE_BUILD_ID, env_extra={"FAKE_LIVE_LABELS": json.dumps(labels)}
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("incremental-skipped: live container serves no salami/gapps release", result.stderr)
        self.assertNotIn("--incremental", self.deploy_log.read_text())

    def test_unknown_channel_is_a_usage_error_before_anything_runs(self) -> None:
        result = self.run_script(channel="salami/nonsense")
        self.assertEqual(64, result.returncode)
        self.assertFalse(self.status.exists())
        self.assertFalse(self.build_log.exists())
        self.assertFalse(self.build_type_log.exists())

    def test_unknown_argument_is_a_usage_error(self) -> None:
        result = subprocess.run([str(SCRIPT), "--bogus"], capture_output=True, text=True,
                                env=os.environ | {"YRRP_STATUS_FILE": str(self.status)})
        self.assertEqual(64, result.returncode)
        self.assertIn("usage:", result.stderr)
        self.assertFalse(self.status.exists())

    def test_gapps_channel_exports_build_type_and_gapps_names(self) -> None:
        result = self.run_script(channel="salami/gapps")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("gapps", self.build_type_log.read_text().strip())
        self.assertTrue((self.signed / f"lineage-23.2-salami-gapps-{BUILD_ID}-signed-ota.zip").exists())
        self.assertTrue((self.signed / f"lineage-23.2-salami-gapps-{BUILD_ID}-SHA256SUMS.txt").exists())
        self.assertIn("--channel salami/gapps", self.deploy_log.read_text())
        self.assertEqual(str(self.signed / "profile" / f"gapps-{BUILD_ID}"), self.profile_log.read_text())

    def test_vanilla_is_the_default_channel(self) -> None:
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("vanilla", self.build_type_log.read_text().strip())
        self.assertIn("--channel salami/vanilla", self.deploy_log.read_text())

    def test_incremental_source_comes_from_the_channels_own_label(self) -> None:
        labels = {"io.yrrp.ota.channel.salami.gapps.build-id": "20990101-000001",
                  "io.yrrp.ota.channel.salami.vanilla.build-id": "20990101-000002"}
        self.signed.mkdir(parents=True, exist_ok=True)
        (self.signed / "lineage-23.2-salami-gapps-20990101-000001-signed-target_files.zip").write_bytes(b"src")
        result = self.run_script(
            channel="salami/gapps", live_build="20990101-000001", env_extra={"FAKE_LIVE_LABELS": json.dumps(labels)}
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--channel salami/gapps --source-build 20990101-000001", self.incremental_log.read_text())
        incremental = f"lineage-23.2-salami-gapps-20990101-000001-to-{BUILD_ID}-signed-incremental-ota.zip"
        self.assertIn(f"--incremental {self.signed / incremental}", self.deploy_log.read_text())

    def test_release_keeps_other_channels_target_files(self) -> None:
        self.signed.mkdir(parents=True, exist_ok=True)
        other = self.signed / "lineage-23.2-salami-20990101-000002-signed-target_files.zip"
        other.write_bytes(b"vanilla-live")
        result = self.run_script(channel="salami/gapps")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"vanilla-live", other.read_bytes())

    def docker_calls(self) -> list[list[str]]:
        if not self.docker_log.exists():
            return []
        return [json.loads(line) for line in self.docker_log.read_text().splitlines()]

    def test_gapps_refused_before_build_when_base_image_lacks_route(self) -> None:
        conf = "location = /updates/salami.json {\n"
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_BASE_NGINX_CONF": conf})
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "base OTA image ghcr.io/yrrp/ota:main does not serve /updates/salami/gapps.json; publish ota_server first",
            result.stderr,
        )
        self.assertTrue(self.status.read_text().startswith("signing-failed:"))
        self.assertFalse(self.build_type_log.exists())
        self.assertFalse(self.build_log.exists())
        self.assertFalse(self.deploy_log.exists())

    def test_gapps_proceeds_when_base_image_serves_route(self) -> None:
        conf = "location = /updates/salami.json {\nlocation = /updates/salami/gapps.json {\n"
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_BASE_NGINX_CONF": conf})
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertIn(["pull", "ghcr.io/yrrp/ota:main"], calls)
        grep = [call for call in calls if call[:1] == ["run"]]
        self.assertEqual(1, len(grep), calls)
        self.assertEqual(["--network", "none"], grep[0][2:4], grep[0])
        self.assertIn("^[[:space:]]*location = /updates/salami/gapps\\.json \\{", grep[0])
        self.assertEqual("gapps", self.build_type_log.read_text().strip())

    def test_gapps_refused_before_build_when_base_image_pull_fails(self) -> None:
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_PULL_FAIL": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot pull base OTA image ghcr.io/yrrp/ota:main", result.stderr)
        self.assertTrue(self.status.read_text().startswith("signing-failed:"))
        self.assertFalse(self.build_type_log.exists())

    def test_gapps_refused_when_base_image_config_cannot_be_read(self) -> None:
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_BASE_RUN_EXIT": "125"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot read nginx config of base OTA image ghcr.io/yrrp/ota:main", result.stderr)
        self.assertTrue(self.status.read_text().startswith("signing-failed:"))
        self.assertFalse(self.build_type_log.exists())

    def test_gapps_refused_when_base_image_route_is_only_commented_out(self) -> None:
        conf = "location = /updates/salami.json {\n# location = /updates/salami/gapps.json {\n"
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_BASE_NGINX_CONF": conf})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not serve /updates/salami/gapps.json", result.stderr)
        self.assertTrue(self.status.read_text().startswith("signing-failed:"))
        self.assertFalse(self.build_type_log.exists())

    def test_gapps_route_check_does_not_treat_dot_as_wildcard(self) -> None:
        conf = "location = /updates/salami/gappsXjson {\n"
        result = self.run_script(channel="salami/gapps", env_extra={"FAKE_BASE_NGINX_CONF": conf})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not serve /updates/salami/gapps.json", result.stderr)

    def test_vanilla_skips_base_image_route_check(self) -> None:
        result = self.run_script(env_extra={"FAKE_BASE_NGINX_CONF": ""})
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertFalse([call for call in calls if call[:1] in (["pull"], ["run"])], calls)

    def test_unreadable_incremental_checksum_stops_before_deploy(self) -> None:
        self._create_live_source()
        result = self.run_script(live_build=SOURCE_BUILD_ID, unreadable_incremental=True)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.deploy_log.exists())
        self.assertNotEqual("complete", self.status.read_text().strip())


if __name__ == "__main__":
    unittest.main()

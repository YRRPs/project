from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sign-lineage-build.sh"
BUILD_ID = "20990101-000000"


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
            "breakfast() { :; }\n"
            "mka() { printf '%s\\n' \"$*\" > \"${YRRP_BUILD_LOG}\"; }\n"
            "sign_target_files_apks() { cp \"${ANDROID_PW_FILE}\" \"${YRRP_PASSWORD_LOG}\"; cp \"${@: -2:1}\" \"${@: -1}\"; }\n"
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
        openssl = self.bin / "openssl"
        openssl.write_text("#!/bin/sh\nprintf synthetic-der\n")
        openssl.chmod(0o755)
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\nexit 0\n")
        docker.chmod(0o755)
        unzip = self.bin / "unzip"
        unzip.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, zipfile\n"
            "args = sys.argv[1:]\n"
            "if args[0] == '-tq': sys.exit(0)\n"
            "if args[0] == '-Z1':\n"
            "    print('\\n'.join(zipfile.ZipFile(args[1]).namelist()))\n"
            "elif args[0] == '-p':\n"
            "    sys.stdout.buffer.write(zipfile.ZipFile(args[1]).read(args[2]))\n"
            "else: sys.exit(2)\n"
        )
        unzip.chmod(0o755)

    def run_script(self, deploy_exit: int = 0) -> subprocess.CompletedProcess[str]:
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
            "YRRP_RUNTIME_PASSWORD_FILE": str(self.root / "runtime-passwords"),
            "OTA_PUBLIC_BASE_URL": "https://ota.example.invalid",
            "OTA_BASE_IMAGE_REF": "ghcr.io/yrrp/ota:main",
        }
        return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)

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


if __name__ == "__main__":
    unittest.main()

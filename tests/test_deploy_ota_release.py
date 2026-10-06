from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.make_release_fixture import BUILD_ID, create_fixture

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/deploy-ota-release.sh"
FAKE_DOCKER = ROOT / "tests/fixtures/fake_docker.py"


class DeployOtaReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ota, self.target = create_fixture(self.root / "input")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shutil.copy2(FAKE_DOCKER, self.bin / "docker")
        (self.bin / "docker").chmod(0o755)
        self.log = self.root / "docker.log"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_deploy(self, mode: str) -> subprocess.CompletedProcess[str]:
        env = os.environ | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_DOCKER_MODE": mode,
            "OTA_PUBLIC_BASE_URL": "https://ota.example.invalid",
            "OTA_BASE_IMAGE_REF": "ghcr.io/yrrps/ota-server:main",
            "OTA_WORK_DIR": str(self.root / "work"),
            "OTA_LOCK_FILE": str(self.root / "deploy.lock"),
            "OTA_HEALTH_TIMEOUT": "1",
            "OTA_HEALTH_INTERVAL": "0",
        }
        return subprocess.run(
            [
                str(DEPLOY),
                "--ota",
                str(self.ota),
                "--target-files",
                str(self.target),
                "--build-id",
                BUILD_ID,
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def commands(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_initial_deployment_builds_hardened_container_without_push(self) -> None:
        result = self.run_deploy("initial")
        self.assertEqual(0, result.returncode, result.stderr)
        commands = self.commands()
        self.assertTrue(any(command[:1] == ["build"] for command in commands))
        run = next(command for command in commands if command[:1] == ["run"])
        self.assertIn("--read-only", run)
        self.assertIn("--cap-drop", run)
        self.assertIn("no-new-privileges:true", run)
        self.assertIn("proxy-net", run)
        self.assertFalse(any(command[:1] == ["push"] for command in commands))

    def test_refuses_to_replace_foreign_container(self) -> None:
        result = self.run_deploy("foreign")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not labeled as YRRP", result.stderr)
        commands = self.commands()
        self.assertFalse(any(command[:1] == ["stop"] for command in commands))

    def assert_previous_restored(self) -> None:
        flattened = [" ".join(command) for command in self.commands()]
        self.assertTrue(any("rename yrrp-ota-server yrrp-ota-server.previous" in command for command in flattened))
        self.assertTrue(any("rename yrrp-ota-server.previous yrrp-ota-server" in command for command in flattened))
        self.assertTrue(any("start yrrp-ota-server" in command for command in flattened))

    def test_unhealthy_candidate_restores_previous_container(self) -> None:
        result = self.run_deploy("unhealthy")
        self.assertNotEqual(0, result.returncode)
        self.assert_previous_restored()
        self.assertIn("previous OTA release restored", result.stderr)

    def test_metadata_verification_failure_restores_previous_container(self) -> None:
        result = self.run_deploy("metadata_fail")
        self.assertNotEqual(0, result.returncode)
        self.assert_previous_restored()

    def test_run_failure_after_rename_restores_previous_container(self) -> None:
        result = self.run_deploy("run_fail")
        self.assertNotEqual(0, result.returncode)
        self.assert_previous_restored()

    def test_signal_after_rename_restores_previous_container(self) -> None:
        result = self.run_deploy("rename_signal")
        self.assertNotEqual(0, result.returncode)
        self.assert_previous_restored()

    def test_signal_during_old_cleanup_keeps_verified_candidate(self) -> None:
        result = self.run_deploy("cleanup_signal")
        self.assertEqual(0, result.returncode, result.stderr)
        flattened = [" ".join(command) for command in self.commands()]
        self.assertFalse(any("rm -f yrrp-ota-server" == command for command in flattened))
        self.assertFalse(any("rename yrrp-ota-server.previous yrrp-ota-server" in command for command in flattened))


if __name__ == "__main__":
    unittest.main()

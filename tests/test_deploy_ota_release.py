from __future__ import annotations

import fcntl
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
    SOURCE_INCREMENTAL,
    create_fixture,
    create_incremental_fixture,
    write_incremental_meta,
)

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

    def run_deploy(self, mode: str, *extra: str, ota_env: bool = True) -> subprocess.CompletedProcess[str]:
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
            "YRRP_BUILD_LOCK_FILE": str(self.root / "build.lock"),
        }
        env.pop("YRRP_BUILD_LOCK_HELD", None)
        if not ota_env:
            env.pop("OTA_PUBLIC_BASE_URL")
            env.pop("OTA_BASE_IMAGE_REF")
        return subprocess.run(
            [
                str(DEPLOY),
                "--ota",
                str(self.ota),
                "--target-files",
                str(self.target),
                "--build-id",
                BUILD_ID,
                *extra,
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

    def test_incremental_deploy_verifies_incremental_and_fallback_routes(self) -> None:
        incremental = create_incremental_fixture(self.root / "input")
        write_incremental_meta(incremental)
        result = self.run_deploy("initial", "--incremental", str(incremental))
        self.assertEqual(0, result.returncode, result.stderr)
        execs = [" ".join(command) for command in self.commands() if command[:1] == ["exec"]]
        self.assertTrue(any(f"/updates/salami/{SOURCE_INCREMENTAL}.json" in c and INCREMENTAL_NAME in c for c in execs))
        self.assertTrue(any("/updates/salami/1.json" in c for c in execs))
        self.assertTrue(any(f"/install/salami/{BUILD_ID}/{INCREMENTAL_NAME}" in c and "206" in c for c in execs))

    def test_missing_fallback_route_restores_previous_container(self) -> None:
        result = self.run_deploy("route_missing")
        self.assertNotEqual(0, result.returncode)
        self.assert_previous_restored()

    def test_uses_yrrp_ota_defaults(self) -> None:
        result = self.run_deploy("initial", ota_env=False)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_refuses_while_campaign_build_holds_lock(self) -> None:
        with (self.root / "build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_deploy("initial")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("A campaign build holds", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
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
sys.path.insert(0, str(ROOT / "scripts"))

from tests.test_carry_over import write_release  # noqa: E402
from yrrp_ota.channel import GAPPS, VANILLA  # noqa: E402

LIVE_VANILLA_BUILD = "20990101-000000"
LIVE_GAPPS_BUILD = "20981231-000000"
VANILLA_LABEL = "io.yrrp.ota.channel.salami.vanilla.build-id"
GAPPS_LABEL = "io.yrrp.ota.channel.salami.gapps.build-id"


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

    def run_deploy(
        self,
        mode: str,
        *extra: str,
        ota_env: bool = True,
        channel: str | None = None,
        ota: Path | None = None,
        target: Path | None = None,
        env_extra: dict[str, str] | None = None,
        deploy: Path = DEPLOY,
    ) -> subprocess.CompletedProcess[str]:
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
        } | (env_extra or {})
        env.pop("YRRP_BUILD_LOCK_HELD", None)
        if not ota_env:
            env.pop("OTA_PUBLIC_BASE_URL")
            env.pop("OTA_BASE_IMAGE_REF")
        channel_args = ["--channel", channel] if channel else []
        return subprocess.run(
            [
                str(deploy),
                *channel_args,
                "--ota",
                str(ota or self.ota),
                "--target-files",
                str(target or self.target),
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

    def test_refuses_while_release_build_holds_lock(self) -> None:
        with (self.root / "build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_deploy("initial")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("A release build holds", result.stderr)
        self.assertFalse(self.log.exists())

    def stage_live(self, *, gapps: bool = False) -> Path:
        """A live /srv/ota serving vanilla, and gapps too when asked."""
        live = self.root / "live"
        write_release(live, VANILLA, LIVE_VANILLA_BUILD)
        if gapps:
            write_release(live, GAPPS, LIVE_GAPPS_BUILD)
        return live

    def live_env(self, live: Path, labels: dict[str, str] | None) -> dict[str, str]:
        env = {"FAKE_LIVE_ROOT": str(live), "FAKE_CANDIDATE_ROOT": str(live)}
        if labels is not None:
            env["FAKE_LIVE_LABELS"] = json.dumps(labels)
        return env

    def deploy_gapps(self, mode: str, labels: dict[str, str] | None, *, live: Path | None = None):
        live = live or self.stage_live()
        ota, target = create_fixture(self.root / "gapps-input", build_type="gapps")
        return self.run_deploy(
            mode, channel="salami/gapps", ota=ota, target=target, env_extra=self.live_env(live, labels)
        )

    def labels_of(self, command: list[str]) -> list[str]:
        return [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--label"]

    def assert_only_channel_labels(self, expected: list[str]) -> None:
        build = next(c for c in self.commands() if c[:1] == ["build"])
        run = next(c for c in self.commands() if c[:1] == ["run"])
        wanted = sorted(["io.yrrp.ota.release=true", *expected])
        self.assertEqual(wanted, sorted(self.labels_of(build)))
        self.assertEqual(wanted, sorted(self.labels_of(run)))

    def release_contexts(self) -> list[Path]:
        work = self.root / "work"
        return sorted(work.glob("release-*")) if work.exists() else []

    def test_gapps_deploy_carries_vanilla_and_labels_both_channels(self) -> None:
        labels = {"io.yrrp.ota.release": "true", VANILLA_LABEL: LIVE_VANILLA_BUILD}
        result = self.deploy_gapps("existing", labels)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_only_channel_labels([f"{VANILLA_LABEL}={LIVE_VANILLA_BUILD}", f"{GAPPS_LABEL}={BUILD_ID}"])
        self.assertIn(f"carried salami/vanilla {LIVE_VANILLA_BUILD}", result.stderr)
        self.assertIn("carried routes unchanged: 1", result.stderr)
        build = next(c for c in self.commands() if c[:1] == ["build"])
        self.assertIn(f"yrrp-ota-release:gapps-{BUILD_ID}", build)

    def test_gapps_deploy_over_legacy_labelled_container_carries_vanilla(self) -> None:
        result = self.deploy_gapps("existing", None)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_only_channel_labels([f"{VANILLA_LABEL}={LIVE_VANILLA_BUILD}", f"{GAPPS_LABEL}={BUILD_ID}"])
        self.assertIn(f"carried salami/vanilla {LIVE_VANILLA_BUILD}", result.stderr)

    def test_vanilla_deploy_carries_live_gapps_and_replaces_live_vanilla(self) -> None:
        live = self.stage_live(gapps=True)
        labels = {"io.yrrp.ota.release": "true", VANILLA_LABEL: LIVE_VANILLA_BUILD, GAPPS_LABEL: LIVE_GAPPS_BUILD}
        result = self.run_deploy("existing", channel="salami/vanilla", env_extra=self.live_env(live, labels))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_only_channel_labels([f"{VANILLA_LABEL}={BUILD_ID}", f"{GAPPS_LABEL}={LIVE_GAPPS_BUILD}"])
        self.assertIn(f"carried salami/gapps {LIVE_GAPPS_BUILD}", result.stderr)

    def test_legacy_vanilla_deploy_writes_only_channel_labels(self) -> None:
        result = self.run_deploy("existing")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_only_channel_labels([f"{VANILLA_LABEL}={BUILD_ID}"])
        build = next(c for c in self.commands() if c[:1] == ["build"])
        self.assertIn(f"yrrp-ota-release:vanilla-{BUILD_ID}", build)

    def test_gapps_deploy_verifies_its_own_fallback_route(self) -> None:
        result = self.deploy_gapps("existing", None)
        self.assertEqual(0, result.returncode, result.stderr)
        execs = [" ".join(command) for command in self.commands() if command[:1] == ["exec"]]
        gapps_ota = GAPPS.full_ota_name(BUILD_ID)
        self.assertTrue(any("/updates/salami/gapps/1.json" in c and gapps_ota in c for c in execs))
        self.assertTrue(any("/updates/salami/gapps.json" in c for c in execs))
        self.assertTrue(any(f"/install/salami/gapps/{BUILD_ID}/{gapps_ota}" in c and "206" in c for c in execs))
        self.assertFalse(any("/updates/salami/1.json" in c for c in execs))

    def test_carry_failure_stops_before_any_container_change(self) -> None:
        labels = {"io.yrrp.ota.release": "true", VANILLA_LABEL: LIVE_VANILLA_BUILD}
        result = self.deploy_gapps("existing", labels, live=self.root / "empty")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("carrying live OTA channels failed", result.stderr)
        verbs = {c[0] for c in self.commands()}
        self.assertFalse(verbs & {"build", "stop", "rename", "run", "rm", "start"}, verbs)
        self.assertEqual([], self.release_contexts())

    def test_unreadable_live_labels_stop_before_any_container_change(self) -> None:
        live = self.stage_live()
        result = self.run_deploy(
            "existing", channel="salami/gapps", env_extra=self.live_env(live, None) | {"FAKE_LIVE_LABELS": "{"}
        )
        self.assertNotEqual(0, result.returncode)
        verbs = {c[0] for c in self.commands()}
        self.assertFalse(verbs & {"build", "stop", "rename", "run", "rm", "start"}, verbs)
        self.assertEqual([], self.release_contexts())

    def test_changed_carried_route_restores_previous_container(self) -> None:
        labels = {"io.yrrp.ota.release": "true", VANILLA_LABEL: LIVE_VANILLA_BUILD}
        result = self.deploy_gapps("carried_route_changed", labels)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("carried route /updates/salami.json changed", result.stderr)
        self.assert_previous_restored()
        flattened = [" ".join(command) for command in self.commands()]
        self.assertIn("rm -f yrrp-ota-server", flattened)
        self.assertEqual([], self.release_contexts())

    def deploy_with_carry_stub(self, labels: list[str]) -> Path:
        """A copy of scripts/ whose ota-carry-over.py prints |labels| and writes a snapshot."""
        scripts = self.root / "scripts"
        shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        stub = scripts / "ota-carry-over.py"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1] != 'carry':\n"
            "    sys.exit(0)\n"
            "context = Path(sys.argv[sys.argv.index('--context') + 1])\n"
            "(context / 'carried.json').write_text('{\"carried\": {}, \"routes\": {}}\\n')\n"
            f"print({chr(10).join(labels)!r})\n"
        )
        return scripts / "deploy-ota-release.sh"

    def test_refuses_carried_label_for_released_channel(self) -> None:
        deploy = self.deploy_with_carry_stub([f"{GAPPS_LABEL}={LIVE_GAPPS_BUILD}"])
        ota, target = create_fixture(self.root / "gapps-input", build_type="gapps")
        result = self.run_deploy("existing", channel="salami/gapps", ota=ota, target=target, deploy=deploy)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("carry-over returned the released channel's label", result.stderr)
        verbs = {c[0] for c in self.commands()}
        self.assertFalse(verbs & {"build", "stop", "rename", "run", "rm", "start"}, verbs)
        self.assertEqual([], self.release_contexts())

    def test_rejects_unknown_channel(self) -> None:
        result = self.run_deploy("existing", channel="salami/nope")
        self.assertEqual(64, result.returncode)
        self.assertIn("usage:", result.stderr)
        self.assertFalse(self.log.exists())

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tests.fixtures.make_release_fixture import (  # noqa: E402
    SOURCE_BUILD_ID,
    SOURCE_INCREMENTAL,
    create_fixture,
    create_incremental_fixture,
    write_incremental_meta,
)
from yrrp_ota.carry_over import CarryOverError, carry_over, verify_routes  # noqa: E402
from yrrp_ota.channel import GAPPS, VANILLA  # noqa: E402

VANILLA_BUILD = "20261009-120000"
GAPPS_BUILD = "20261010-120000"
OLD_BUILD = "20261001-000000"
BASE_URL = "https://ota.example.invalid"
CLI = ROOT / "scripts/ota-carry-over.py"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_file(path: Path, data: bytes, role: str) -> dict:
    path.write_bytes(data)
    return {"filename": path.name, "role": role, "sha256": sha(data), "size": len(data)}


def updater_entry(record: dict, url: str) -> list[dict]:
    file = {"filename": record["filename"], "sha256": record["sha256"], "size": record["size"], "url": url}
    return [{"datetime": 1, "files": [file], "type": "UNOFFICIAL", "version": "23.2"}]


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_release(root: Path, channel, build_id: str, *, incremental: str | None = None, schema: int = 3) -> None:
    """Write a served release tree shaped like prepare-ota-release.py output."""
    install = root / channel.install_dir(build_id)
    install.mkdir(parents=True)
    base_url = f"{BASE_URL}/{channel.install_dir(build_id)}"
    ota = write_file(install / channel.full_ota_name(build_id), b"ota-" + build_id.encode(), "ota")
    artifacts = [ota]
    manifest_incremental = None
    if incremental:
        name = channel.incremental_ota_name(SOURCE_BUILD_ID, build_id)
        record = write_file(install / name, b"incremental-" + build_id.encode(), "incremental-ota")
        artifacts.append(record)
        manifest_incremental = {**record, "source_build_id": SOURCE_BUILD_ID, "source_incremental": incremental}
        del manifest_incremental["role"]
        write_json(root / channel.updates_incremental_path(incremental), updater_entry(record, f"{base_url}/{name}"))
    artifacts.append(write_file(install / "boot.img", b"boot-" + build_id.encode(), "boot"))
    release = {"artifacts": artifacts, "build_id": build_id, "schema": schema, "incremental": manifest_incremental}
    if schema >= 3:
        release["channel"] = channel.name
    write_json(install / "release.json", release)
    sums = "".join(f"{a['sha256']}  {a['filename']}\n" for a in artifacts)
    (install / "SHA256SUMS.txt").write_text(sums)
    write_json(root / channel.updates_full_path, updater_entry(ota, f"{base_url}/{ota['filename']}"))


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("prepare_ota_release", ROOT / "scripts/prepare-ota-release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CarryOverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "live"
        self.context = self.root / "context"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        shutil.copy2(ROOT / "tests/fixtures/fake_channel_docker.py", bin_dir / "docker")
        (bin_dir / "docker").chmod(0o755)
        self.env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LIVE_ROOT": str(self.live)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_carry(self, labels, releasing=GAPPS):
        self.env["FAKE_LIVE_LABELS"] = labels if isinstance(labels, str) else json.dumps(labels)
        return carry_over("yrrp-ota-server", releasing, self.context, env=self.env)

    def install(self, channel, build_id: str) -> Path:
        return self.live / channel.install_dir(build_id)

    def edit_json(self, path: Path, edit) -> None:
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))

    def edit_release(self, channel, build_id: str, edit) -> None:
        self.edit_json(self.install(channel, build_id) / "release.json", edit)

    def test_copies_other_channels_and_skips_the_released_one(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        write_release(self.live, GAPPS, OLD_BUILD)
        result = self.run_carry({VANILLA.label: VANILLA_BUILD, GAPPS.label: OLD_BUILD})
        self.assertEqual({VANILLA: VANILLA_BUILD}, result.carried)
        rootfs = self.context / "rootfs"
        self.assertTrue((rootfs / VANILLA.updates_full_path).is_file())
        self.assertTrue((rootfs / VANILLA.updates_incremental_path("42")).is_file())
        install = rootfs / VANILLA.install_dir(VANILLA_BUILD)
        self.assertTrue((install / VANILLA.full_ota_name(VANILLA_BUILD)).is_file())
        self.assertTrue((install / "boot.img").is_file())
        self.assertFalse((rootfs / GAPPS.updates_full_path).exists())
        self.assertEqual({VANILLA.label: VANILLA_BUILD}, result.labels)
        self.assertEqual({VANILLA.updates_full_path, VANILLA.updates_incremental_path("42")}, set(result.snapshot))

    def test_legacy_labels_carry_schema_2_vanilla(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, schema=2, incremental="42")
        result = self.run_carry({"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": VANILLA_BUILD})
        self.assertEqual({VANILLA: VANILLA_BUILD}, result.carried)
        self.assertEqual({VANILLA.label: VANILLA_BUILD}, result.labels)

    def test_build_id_mismatch_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        self.edit_release(VANILLA, VANILLA_BUILD, lambda data: data.update(build_id="20000101-000000"))
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*build_id"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_checksum_mismatch_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        (self.install(VANILLA, VANILLA_BUILD) / VANILLA.full_ota_name(VANILLA_BUILD)).write_bytes(b"tampered")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*SHA-256"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_channel_field_mismatch_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        self.edit_release(VANILLA, VANILLA_BUILD, lambda data: data.update(channel="salami/gapps"))
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*channel"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_schema_3_without_channel_field_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        self.edit_release(VANILLA, VANILLA_BUILD, lambda data: data.pop("channel"))
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*channel"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_schema_2_is_only_accepted_for_vanilla(self) -> None:
        write_release(self.live, GAPPS, GAPPS_BUILD, schema=2)
        with self.assertRaisesRegex(CarryOverError, "salami/gapps.*schema"):
            self.run_carry({GAPPS.label: GAPPS_BUILD}, releasing=VANILLA)

    def test_container_without_labels_carries_nothing(self) -> None:
        for labels in ("null", "{}"):
            with self.subTest(labels=labels):
                result = self.run_carry(labels)
                self.assertEqual({}, result.carried)
                self.assertEqual({}, result.snapshot)

    def test_only_the_released_channel_live_carries_nothing(self) -> None:
        write_release(self.live, GAPPS, OLD_BUILD)
        result = self.run_carry({GAPPS.label: OLD_BUILD})
        self.assertEqual({}, result.carried)
        self.assertFalse((self.context / "rootfs").exists())

    def test_inspect_failure_fails_closed(self) -> None:
        self.env["FAKE_INSPECT_FAIL"] = "1"
        with self.assertRaisesRegex(CarryOverError, "inspect"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_non_object_labels_fail_closed(self) -> None:
        for labels in ("[]", '"x"', "not json"):
            with self.subTest(labels=labels), self.assertRaisesRegex(CarryOverError, "labels"):
                self.run_carry(labels)

    def test_unknown_or_partial_labels_fail_closed(self) -> None:
        cases = (
            {"io.yrrp.ota.channel.salami.microg.build-id": VANILLA_BUILD},
            {"io.yrrp.ota.device": "salami"},
            {VANILLA.label: "latest"},
        )
        for labels in cases:
            with self.subTest(labels=labels), self.assertRaisesRegex(CarryOverError, "labels"):
                self.run_carry(labels)

    def test_checksum_list_must_name_exactly_the_artifacts(self) -> None:
        extra = f"{'0' * 64}  extra.img\n"
        cases = {
            "extra": lambda text: text + extra,
            "missing": lambda text: "".join(text.splitlines(keepends=True)[:-1]),
            "malformed": lambda text: text + "not a checksum line\n",
            "duplicate": lambda text: text + text.splitlines(keepends=True)[0],
        }
        for case, edit in cases.items():
            with self.subTest(case=case):
                shutil.rmtree(self.live, ignore_errors=True)
                shutil.rmtree(self.context, ignore_errors=True)
                write_release(self.live, VANILLA, VANILLA_BUILD)
                sums = self.install(VANILLA, VANILLA_BUILD) / "SHA256SUMS.txt"
                sums.write_text(edit(sums.read_text()))
                with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*SHA256SUMS"):
                    self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_unsafe_artifact_filename_is_refused_before_copy(self) -> None:
        for name in ("../x", "sub/x", "", "..", "."):
            with self.subTest(name=name):
                shutil.rmtree(self.live, ignore_errors=True)
                shutil.rmtree(self.context, ignore_errors=True)
                write_release(self.live, VANILLA, VANILLA_BUILD)
                (self.live / VANILLA.install_dir(VANILLA_BUILD)).parent.joinpath("x").write_bytes(b"x")
                self.edit_release(VANILLA, VANILLA_BUILD, lambda data: data["artifacts"][-1].update(filename=name))
                with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*filename"):
                    self.run_carry({VANILLA.label: VANILLA_BUILD})
                self.assertFalse((self.context / "rootfs/install/salami/x").exists())

    def test_full_entry_pointing_at_another_build_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        other = f"{BASE_URL}/{VANILLA.install_dir(OLD_BUILD)}/{VANILLA.full_ota_name(VANILLA_BUILD)}"
        self.edit_json(self.live / VANILLA.updates_full_path, lambda data: data[0]["files"][0].update(url=other))
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*updates/salami.json.*url"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_incremental_entry_pointing_at_another_build_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        name = VANILLA.incremental_ota_name(SOURCE_BUILD_ID, VANILLA_BUILD)
        other = f"{BASE_URL}/{VANILLA.install_dir(OLD_BUILD)}/{name}"
        route = self.live / VANILLA.updates_incremental_path("42")
        self.edit_json(route, lambda data: data[0]["files"][0].update(url=other))
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*updates/salami/42.json.*url"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_malformed_updates_entry_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        (self.live / VANILLA.updates_full_path).write_text("{}\n")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*updates/salami.json"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_missing_live_file_fails_closed(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        (self.install(VANILLA, VANILLA_BUILD) / "boot.img").unlink()
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*cannot copy.*boot.img"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_refuses_to_overwrite_a_file_already_in_the_context(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        existing = self.context / "rootfs" / VANILLA.updates_full_path
        existing.parent.mkdir(parents=True)
        existing.write_text("[]\n")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*already exists"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})
        self.assertEqual("[]\n", existing.read_text())

    def plant_symlink(self, path: Path) -> None:
        """Replace a live file with a symlink to an identical file outside the served tree."""
        outside = self.root / "host" / path.name
        outside.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, outside)
        path.unlink()
        path.symlink_to(outside)

    def test_symlinked_artifact_is_refused(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        self.plant_symlink(self.install(VANILLA, VANILLA_BUILD) / "boot.img")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*boot.img.*yrrp-ota-server.*not a regular file"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_symlinked_release_json_is_refused(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        self.plant_symlink(self.install(VANILLA, VANILLA_BUILD) / "release.json")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*release.json.*not a regular file"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_directory_where_a_file_is_expected_is_refused(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD)
        boot = self.install(VANILLA, VANILLA_BUILD) / "boot.img"
        boot.unlink()
        boot.mkdir()
        (boot / "inner").write_bytes(b"x")
        with self.assertRaisesRegex(CarryOverError, "salami/vanilla.*boot.img.*not a regular file"):
            self.run_carry({VANILLA.label: VANILLA_BUILD})

    def test_cli_verify_routes_refuses_snapshot_missing_a_full_route(self) -> None:
        self.context.mkdir()
        snapshot = self.context / "carried.json"
        snapshot.write_text(json.dumps({"carried": {"salami/vanilla": VANILLA_BUILD}, "routes": {}}))
        self.env["FAKE_CANDIDATE_ROOT"] = str(self.context / "rootfs")
        verify = subprocess.run(
            [sys.executable, str(CLI), "verify-routes", "--container", "c", "--snapshot", str(snapshot)],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(1, verify.returncode)
        self.assertIn("salami/vanilla", verify.stderr)
        self.assertIn(VANILLA.updates_full_path, verify.stderr)

    def test_verify_routes_passes_on_identical_bodies_and_fails_on_change(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        result = self.run_carry({VANILLA.label: VANILLA_BUILD})
        self.env["FAKE_CANDIDATE_ROOT"] = str(self.context / "rootfs")
        verify_routes("yrrp-ota-server", result.snapshot, env=self.env)
        (self.context / "rootfs" / VANILLA.updates_full_path).write_text("[]\n")
        with self.assertRaisesRegex(CarryOverError, "changed"):
            verify_routes("yrrp-ota-server", result.snapshot, env=self.env)
        (self.context / "rootfs" / VANILLA.updates_incremental_path("42")).unlink()
        with self.assertRaisesRegex(CarryOverError, "not served"):
            verify_routes("yrrp-ota-server", {VANILLA.updates_incremental_path("42"): "0" * 64}, env=self.env)

    def test_accepts_genuine_prepare_output_for_both_channels(self) -> None:
        prepare = load_prepare_module()
        build_id = "20990101-000000"
        for channel in (VANILLA, GAPPS):
            inputs = self.root / f"input-{channel.type}"
            ota, target = create_fixture(inputs, build_type=None if channel.is_vanilla else channel.type)
            incremental = create_incremental_fixture(inputs, name=channel.incremental_ota_name(SOURCE_BUILD_ID, build_id))
            write_incremental_meta(incremental)
            output = prepare.prepare_release(
                ota=ota, target_files=target, build_id=build_id, public_base_url=BASE_URL + "/",
                base_image_digest="ghcr.io/yrrp/ota@sha256:" + "a" * 64,
                output=self.root / f"out-{channel.type}", incremental=incremental, channel=channel,
            )
            shutil.copytree(output / "rootfs", self.live, dirs_exist_ok=True)
        for releasing, carried in ((GAPPS, VANILLA), (VANILLA, GAPPS)):
            with self.subTest(carried=carried.name):
                shutil.rmtree(self.context, ignore_errors=True)
                result = self.run_carry({VANILLA.label: build_id, GAPPS.label: build_id}, releasing=releasing)
                self.assertEqual({carried: build_id}, result.carried)
                self.assertIn(carried.updates_incremental_path(SOURCE_INCREMENTAL), result.snapshot)

    def test_cli_carries_and_verifies_routes(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        self.env["FAKE_LIVE_LABELS"] = json.dumps({VANILLA.label: VANILLA_BUILD, GAPPS.label: OLD_BUILD})
        self.context.mkdir()
        carry = subprocess.run(
            [sys.executable, str(CLI), "carry", "--container", "c", "--channel", "salami/gapps", "--context", str(self.context)],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(0, carry.returncode, carry.stderr)
        self.assertEqual(f"{VANILLA.label}={VANILLA_BUILD}\n", carry.stdout)
        snapshot = json.loads((self.context / "carried.json").read_text())
        self.assertEqual({"salami/vanilla": VANILLA_BUILD}, snapshot["carried"])
        self.env["FAKE_CANDIDATE_ROOT"] = str(self.context / "rootfs")
        verify = subprocess.run(
            [sys.executable, str(CLI), "verify-routes", "--container", "c", "--snapshot", str(self.context / "carried.json")],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(0, verify.returncode, verify.stderr)
        self.assertIn("carried routes unchanged: 2", verify.stderr)

    def test_verify_routes_reports_each_unchanged_route_in_order(self) -> None:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        result = self.run_carry({VANILLA.label: VANILLA_BUILD})
        self.env["FAKE_CANDIDATE_ROOT"] = str(self.context / "rootfs")
        lines: list[str] = []
        verify_routes("yrrp-ota-server", result.snapshot, env=self.env, report=lines.append)
        self.assertEqual(
            [
                f"carried route /{VANILLA.updates_full_path} unchanged",
                f"carried route /{VANILLA.updates_incremental_path('42')} unchanged",
            ],
            lines,
        )

    def cli_carry_then_verify(self, change_route: str | None = None) -> subprocess.CompletedProcess[str]:
        write_release(self.live, VANILLA, VANILLA_BUILD, incremental="42")
        self.env["FAKE_LIVE_LABELS"] = json.dumps({VANILLA.label: VANILLA_BUILD})
        self.context.mkdir()
        carry = subprocess.run(
            [sys.executable, str(CLI), "carry", "--container", "c", "--channel", "salami/gapps", "--context", str(self.context)],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(0, carry.returncode, carry.stderr)
        if change_route:
            (self.context / "rootfs" / change_route).write_text("[]\n")
        self.env["FAKE_CANDIDATE_ROOT"] = str(self.context / "rootfs")
        return subprocess.run(
            [sys.executable, str(CLI), "verify-routes", "--container", "c", "--snapshot", str(self.context / "carried.json")],
            capture_output=True, text=True, env=self.env,
        )

    def test_cli_verify_routes_logs_each_route_and_the_count(self) -> None:
        verify = self.cli_carry_then_verify()
        self.assertEqual(0, verify.returncode, verify.stderr)
        self.assertEqual(
            [
                f"carried route /{VANILLA.updates_full_path} unchanged",
                f"carried route /{VANILLA.updates_incremental_path('42')} unchanged",
                "carried routes unchanged: 2",
            ],
            verify.stderr.splitlines(),
        )

    def test_cli_verify_routes_logs_the_changed_route(self) -> None:
        changed = VANILLA.updates_incremental_path("42")
        verify = self.cli_carry_then_verify(change_route=changed)
        self.assertEqual(1, verify.returncode)
        self.assertEqual(
            [
                f"carried route /{VANILLA.updates_full_path} unchanged",
                f"ota-carry-over: carried route /{changed} changed",
            ],
            verify.stderr.splitlines(),
        )

    def test_cli_reports_failure_with_exit_1(self) -> None:
        self.env["FAKE_INSPECT_FAIL"] = "1"
        self.context.mkdir()
        carry = subprocess.run(
            [sys.executable, str(CLI), "carry", "--container", "c", "--channel", "salami/gapps", "--context", str(self.context)],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(1, carry.returncode)
        self.assertIn("ota-carry-over:", carry.stderr)
        self.assertFalse((self.context / "carried.json").exists())


if __name__ == "__main__":
    unittest.main()

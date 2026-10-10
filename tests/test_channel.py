from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from yrrp_ota import channel as channel_module  # noqa: E402
from yrrp_ota.channel import ALLOWED, GAPPS, VANILLA, Channel, live_channels  # noqa: E402

BUILD = "20261010-120000"
SOURCE = "20261009-120000"
BAD_VALUES = ("../../etc", "latest", "", "1/2")


class ChannelTest(unittest.TestCase):
    def test_parse_accepts_allowlisted_channels(self) -> None:
        self.assertEqual(VANILLA, Channel.parse("salami/vanilla"))
        self.assertEqual(GAPPS, Channel.parse("salami/gapps"))

    def test_parse_rejects_unknown_channels(self) -> None:
        for value in ("salami", "salami/kernelsu", "xueying/vanilla", "salami/gapps/x", "../vanilla"):
            with self.assertRaises(ValueError):
                Channel.parse(value)

    def test_vanilla_names_are_unchanged(self) -> None:
        self.assertEqual(f"lineage-23.2-salami-{BUILD}-signed-ota.zip", VANILLA.full_ota_name(BUILD))
        self.assertEqual(
            f"lineage-23.2-salami-{BUILD}-signed-target_files.zip", VANILLA.target_files_name(BUILD)
        )
        self.assertEqual(
            f"lineage-23.2-salami-{SOURCE}-to-{BUILD}-signed-incremental-ota.zip",
            VANILLA.incremental_ota_name(SOURCE, BUILD),
        )
        self.assertEqual(f"lineage-23.2-salami-{BUILD}-SHA256SUMS.txt", VANILLA.checksums_name(BUILD))

    def test_gapps_names_carry_the_type(self) -> None:
        self.assertEqual(f"lineage-23.2-salami-gapps-{BUILD}-signed-ota.zip", GAPPS.full_ota_name(BUILD))
        self.assertEqual(
            f"lineage-23.2-salami-gapps-{SOURCE}-to-{BUILD}-signed-incremental-ota.zip",
            GAPPS.incremental_ota_name(SOURCE, BUILD),
        )

    def test_vanilla_paths_are_unchanged(self) -> None:
        self.assertEqual("updates/salami.json", VANILLA.updates_full_path)
        self.assertEqual("updates/salami/7.json", VANILLA.updates_incremental_path("7"))
        self.assertEqual(f"install/salami/{BUILD}", VANILLA.install_dir(BUILD))

    def test_gapps_paths(self) -> None:
        self.assertEqual("updates/salami/gapps.json", GAPPS.updates_full_path)
        self.assertEqual("updates/salami/gapps/7.json", GAPPS.updates_incremental_path("7"))
        self.assertEqual(f"install/salami/gapps/{BUILD}", GAPPS.install_dir(BUILD))

    def test_labels(self) -> None:
        self.assertEqual("io.yrrp.ota.channel.salami.vanilla.build-id", VANILLA.label)
        self.assertEqual("io.yrrp.ota.channel.salami.gapps.build-id", GAPPS.label)

    def test_live_channels_reads_channel_labels(self) -> None:
        labels = {GAPPS.label: BUILD, VANILLA.label: SOURCE, "io.yrrp.ota.release": "true"}
        self.assertEqual({GAPPS: BUILD, VANILLA: SOURCE}, live_channels(labels))

    def test_live_channels_maps_legacy_labels_to_vanilla(self) -> None:
        labels = {"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": SOURCE}
        self.assertEqual({VANILLA: SOURCE}, live_channels(labels))

    def test_live_channels_rejects_bad_build_ids(self) -> None:
        with self.assertRaises(ValueError):
            live_channels({GAPPS.label: "latest"})

    def test_live_channels_rejects_unknown_channel_labels(self) -> None:
        with self.assertRaises(ValueError):
            live_channels({"io.yrrp.ota.channel.salami.kernelsu.build-id": BUILD})

    def test_name_and_is_vanilla(self) -> None:
        self.assertEqual("salami/vanilla", VANILLA.name)
        self.assertEqual("salami/gapps", GAPPS.name)
        self.assertTrue(VANILLA.is_vanilla)
        self.assertFalse(GAPPS.is_vanilla)

    def test_incremental_dirs(self) -> None:
        self.assertEqual("updates/salami", VANILLA.updates_incremental_dir)
        self.assertEqual("updates/salami/gapps", GAPPS.updates_incremental_dir)

    def test_gapps_target_files_and_checksums_names(self) -> None:
        self.assertEqual(
            f"lineage-23.2-salami-gapps-{BUILD}-signed-target_files.zip", GAPPS.target_files_name(BUILD)
        )
        self.assertEqual(f"lineage-23.2-salami-gapps-{BUILD}-SHA256SUMS.txt", GAPPS.checksums_name(BUILD))

    def test_name_and_path_methods_reject_bad_build_ids(self) -> None:
        for channel in (VANILLA, GAPPS):
            for bad in BAD_VALUES:
                with self.subTest(channel=channel.name, bad=bad):
                    for call in (
                        lambda: channel.target_files_name(bad),
                        lambda: channel.full_ota_name(bad),
                        lambda: channel.checksums_name(bad),
                        lambda: channel.install_dir(bad),
                        lambda: channel.incremental_ota_name(bad, BUILD),
                        lambda: channel.incremental_ota_name(SOURCE, bad),
                    ):
                        with self.assertRaises(ValueError):
                            call()

    def test_incremental_path_requires_numeric_incremental(self) -> None:
        for channel in (VANILLA, GAPPS):
            for bad in BAD_VALUES:
                with self.subTest(channel=channel.name, bad=bad):
                    with self.assertRaises(ValueError):
                        channel.updates_incremental_path(bad)

    def test_legacy_labels_beside_channel_labels_must_match_vanilla(self) -> None:
        legacy = {"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": SOURCE}
        labels = {VANILLA.label: SOURCE, GAPPS.label: BUILD, **legacy}
        self.assertEqual({VANILLA: SOURCE, GAPPS: BUILD}, live_channels(labels))

    def test_legacy_labels_beside_only_gapps_raise(self) -> None:
        legacy = {"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": SOURCE}
        with self.assertRaisesRegex(ValueError, "legacy"):
            live_channels({GAPPS.label: BUILD, **legacy})

    def test_legacy_labels_mismatching_vanilla_label_raise(self) -> None:
        legacy = {"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": SOURCE}
        with self.assertRaisesRegex(ValueError, "legacy"):
            live_channels({VANILLA.label: BUILD, **legacy})

    def test_bad_legacy_labels_beside_channel_labels_raise(self) -> None:
        bogus = {"io.yrrp.ota.device": "bogus", "io.yrrp.ota.build-id": "latest"}
        with self.assertRaises(ValueError):
            live_channels({GAPPS.label: BUILD, **bogus})

    def test_no_labels_means_nothing_live(self) -> None:
        self.assertEqual({}, live_channels({}))
        self.assertEqual({}, live_channels({"io.yrrp.ota.release": "true"}))

    def test_bad_legacy_labels_raise(self) -> None:
        cases = {
            "other device": {"io.yrrp.ota.device": "xueying", "io.yrrp.ota.build-id": SOURCE},
            "device without build id": {"io.yrrp.ota.device": "salami"},
            "build id without device": {"io.yrrp.ota.build-id": SOURCE},
            "bad build id": {"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": "latest"},
        }
        for name, labels in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    live_channels(labels)

    def test_every_allowed_channel_round_trips_through_its_label(self) -> None:
        for device, channel_type in ALLOWED:
            channel = Channel(device, channel_type)
            self.assertEqual({channel: BUILD}, live_channels({channel.label: BUILD}))

    def test_non_string_inputs_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            channel_module.require_build_id(5, "build ID")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            GAPPS.updates_incremental_path(7)  # type: ignore[arg-type]

    def test_allowlist_validation_rejects_unsafe_values(self) -> None:
        for bad in ({("salami", "gap.ps")}, {("Salami", "gapps")}, {("salami", "")}, {("sal/ami", "x")}):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    channel_module.validate_allowlist(frozenset(bad))
        channel_module.validate_allowlist(ALLOWED)


CLI = Path(__file__).resolve().parents[1] / "scripts/ota-channel.py"


def channel_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True)


class ChannelCliTest(unittest.TestCase):
    def test_prints_names_and_paths(self) -> None:
        cases = {
            ("--channel", "salami/gapps", "type"): "gapps",
            ("--channel", "salami/gapps", "device"): "salami",
            ("--channel", "salami/gapps", "label"): GAPPS.label,
            ("--channel", "salami/gapps", "target-files", BUILD): GAPPS.target_files_name(BUILD),
            ("--channel", "salami/gapps", "ota", BUILD): GAPPS.full_ota_name(BUILD),
            ("--channel", "salami/gapps", "incremental", SOURCE, BUILD): GAPPS.incremental_ota_name(SOURCE, BUILD),
            ("--channel", "salami/gapps", "checksums", BUILD): GAPPS.checksums_name(BUILD),
            ("--channel", "salami/gapps", "install-dir", BUILD): GAPPS.install_dir(BUILD),
            ("--channel", "salami/gapps", "updates-full"): GAPPS.updates_full_path,
            ("--channel", "salami/gapps", "updates-incremental", "7"): GAPPS.updates_incremental_path("7"),
        }
        for args, expected in cases.items():
            result = channel_cli(*args)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(expected, result.stdout.strip(), args)

    def test_prints_vanilla_names_and_paths(self) -> None:
        cases = {
            ("type",): "vanilla",
            ("device",): "salami",
            ("label",): VANILLA.label,
            ("target-files", BUILD): VANILLA.target_files_name(BUILD),
            ("ota", BUILD): VANILLA.full_ota_name(BUILD),
            ("incremental", SOURCE, BUILD): VANILLA.incremental_ota_name(SOURCE, BUILD),
            ("checksums", BUILD): VANILLA.checksums_name(BUILD),
            ("install-dir", BUILD): VANILLA.install_dir(BUILD),
            ("updates-full",): VANILLA.updates_full_path,
            ("updates-incremental", "7"): VANILLA.updates_incremental_path("7"),
        }
        for args, expected in cases.items():
            result = channel_cli("--channel", "salami/vanilla", *args)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(expected, result.stdout.strip(), args)

    def test_rejects_unknown_channel(self) -> None:
        result = channel_cli("--channel", "salami/kernelsu", "type")
        self.assertEqual(2, result.returncode)
        self.assertIn("unknown channel", result.stderr)

    def test_rejects_bad_values_without_traceback(self) -> None:
        bad_calls = (
            ("ota", "latest"),
            ("target-files", "latest"),
            ("checksums", "latest"),
            ("install-dir", "../x"),
            ("incremental", "latest", BUILD),
            ("incremental", SOURCE, "../x"),
            ("updates-incremental", "../7"),
            ("ota",),
            ("ota", BUILD, BUILD),
        )
        for args in bad_calls:
            result = channel_cli("--channel", "salami/gapps", *args)
            self.assertEqual(2, result.returncode, args)
            self.assertIn("ota-channel:", result.stderr, args)
            self.assertNotIn("Traceback", result.stderr, args)

    def test_live_build_reads_channel_and_legacy_labels(self) -> None:
        legacy = json.dumps({"io.yrrp.ota.device": "salami", "io.yrrp.ota.build-id": SOURCE})
        labels = json.dumps({GAPPS.label: BUILD})
        cases = (
            ("salami/vanilla", legacy, SOURCE),
            ("salami/gapps", legacy, ""),
            ("salami/gapps", labels, BUILD),
            ("salami/vanilla", labels, ""),
            ("salami/gapps", "null", ""),
            ("salami/gapps", "{}", ""),
        )
        for channel, label_json, expected in cases:
            result = channel_cli("--channel", channel, "live-build", label_json)
            self.assertEqual(0, result.returncode, (channel, label_json, result.stderr))
            self.assertEqual("", result.stderr)
            self.assertEqual(expected, result.stdout.strip(), (channel, label_json))

    def test_live_build_rejects_empty_argument(self) -> None:
        result = channel_cli("--channel", "salami/gapps", "live-build", "")
        self.assertEqual(2, result.returncode)
        self.assertIn("ota-channel:", result.stderr)

    def test_live_build_fails_closed_on_unknown_channel_label(self) -> None:
        labels = json.dumps({GAPPS.label: BUILD, "io.yrrp.ota.channel.salami.kernelsu.build-id": BUILD})
        result = channel_cli("--channel", "salami/gapps", "live-build", labels)
        self.assertEqual(2, result.returncode)
        self.assertIn("unknown channel", result.stderr)

    def test_unknown_field_uses_program_prefix(self) -> None:
        result = channel_cli("--channel", "salami/gapps", "bogus")
        self.assertEqual(2, result.returncode)
        self.assertIn("ota-channel:", result.stderr)

    def test_live_build_rejects_malformed_or_partial_labels(self) -> None:
        non_string_build = json.dumps({GAPPS.label: 5})
        for labels in ("not json", "[]", json.dumps({"io.yrrp.ota.device": "salami"}), non_string_build):
            result = channel_cli("--channel", "salami/vanilla", "live-build", labels)
            self.assertEqual(2, result.returncode, labels)
            self.assertIn("ota-channel:", result.stderr, labels)
            self.assertNotIn("Traceback", result.stderr, labels)


if __name__ == "__main__":
    unittest.main()

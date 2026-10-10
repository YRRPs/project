from __future__ import annotations

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

    def test_channel_labels_take_precedence_over_legacy_labels(self) -> None:
        labels = {
            GAPPS.label: BUILD,
            "io.yrrp.ota.device": "bogus",
            "io.yrrp.ota.build-id": "latest",
        }
        self.assertEqual({GAPPS: BUILD}, live_channels(labels))

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

    def test_allowlist_validation_rejects_unsafe_values(self) -> None:
        for bad in ({("salami", "gap.ps")}, {("Salami", "gapps")}, {("salami", "")}, {("sal/ami", "x")}):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    channel_module.validate_allowlist(frozenset(bad))
        channel_module.validate_allowlist(ALLOWED)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from yrrp_ota.channel import GAPPS, VANILLA, Channel, live_channels  # noqa: E402

BUILD = "20261010-120000"
SOURCE = "20261009-120000"


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


if __name__ == "__main__":
    unittest.main()

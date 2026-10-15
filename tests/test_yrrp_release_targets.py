from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from yrrp_release.targets import (
    SUPPORTED_DEVICES,
    TARGETS,
    list_targets,
    parse_targets,
    validate_device,
)


class TargetRegistryTest(unittest.TestCase):
    def test_registry_is_exact_and_deterministic(self) -> None:
        self.assertEqual(("salami", "xueying"), SUPPORTED_DEVICES)
        self.assertEqual(
            [
                "atest:SettingsProviderTest",
                "atest:SystemUITests_presubmit",
                "atest:SystemUiRoboTests",
                "atest:ThemeOverlayApplierTest",
                "atest:ThemePickerRoboTests",
                "atest:monet_tests",
                "m:Settings",
                "m:SettingsProvider",
                "m:SettingsRoboTests",
                "m:SystemUI",
                "m:SystemUI-core",
                "m:ThemePicker",
                "m:monet",
            ],
            list(TARGETS),
        )
        self.assertEqual(
            {"devices": ["salami", "xueying"], "targets": list(TARGETS)},
            list_targets(),
        )

    def test_parses_fixed_targets_without_free_arguments(self) -> None:
        parsed = parse_targets(["m:ThemePicker", "atest:ThemePickerRoboTests"])
        self.assertEqual(("m", "ThemePicker"), (parsed[0].executor, parsed[0].argument))
        self.assertEqual(
            ("atest", "ThemePickerRoboTests"),
            (parsed[1].executor, parsed[1].argument),
        )

    def test_rejects_unknown_duplicate_or_injected_targets(self) -> None:
        for values in (
            [],
            ["m:Unknown"],
            ["m:ThemePicker", "m:ThemePicker"],
            ["m:ThemePicker other"],
            ["m:ThemePicker;id"],
            ["atest:ThemePickerRoboTests --iterations 2"],
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_targets(values)

    def test_validates_device_allowlist(self) -> None:
        self.assertEqual("salami", validate_device("salami"))
        for value in ("", "oneplus11", "salami;id", "salami xueying"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_device(value)


if __name__ == "__main__":
    unittest.main()

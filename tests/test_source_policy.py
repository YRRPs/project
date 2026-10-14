from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PATCH_ROOT = ROOT / "patches" / "frameworks-base"
SHIPPED_FEATURES = (
    "Pulse visualizer",
    "Screen-off effects",
    "Matrix boot and shutdown animation",
    "Fold animations",
    "Ad blocking",
    "Hotspot client management",
    "Full theming",
    "Indoor auto-brightness tuning",
    "OnePlus Camera integration",
    "OnePlus Gallery",
    "GApps integration",
)


class SourcePolicyTest(unittest.TestCase):
    def test_readme_lists_scoped_feature_sections(self) -> None:
        text = README.read_text(encoding="utf-8")
        for heading in (
            "## Features",
            "### Available on all supported devices",
            "### Foldable-only",
            "### Salami-specific",
            "### Build-flavor dependent",
        ):
            self.assertIn(heading, text)

    def test_readme_lists_every_shipped_feature_family(self) -> None:
        text = README.read_text(encoding="utf-8")
        for feature in SHIPPED_FEATURES:
            self.assertIn(feature, text)

    def test_inventory_design_lists_every_shipped_feature_family(self) -> None:
        text = (ROOT / "docs" / "design" / "rom-feature-inventory.md").read_text(
            encoding="utf-8"
        )
        for feature in SHIPPED_FEATURES:
            self.assertIn(feature, text)

    def test_inventory_design_scopes_new_release_features(self) -> None:
        text = (ROOT / "docs" / "design" / "rom-feature-inventory.md").read_text(
            encoding="utf-8"
        )
        availability = text.split("## Availability rules", 1)[1].split(
            "## Observability", 1
        )[0]
        robotests = text.split("Settings robotests cover:", 1)[1].split(
            "Project tests cover:", 1
        )[0]
        device_checks = text.split("Post-release device checks cover:", 1)[1].split(
            "Skip TalkBack", 1
        )[0]

        for section, phrase in (
            (availability, "full theming are available on every supported build"),
            (
                availability,
                "OnePlus Gallery requires the Salami product configuration and installed Gallery package",
            ),
            (robotests, "including full theming"),
            (
                robotests,
                "Salami, non-Salami, present-package, and missing-package OnePlus Gallery behavior",
            ),
            (device_checks, "Full theming availability"),
            (device_checks, "OnePlus Gallery product and package detection"),
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_readme_scopes_new_release_features(self) -> None:
        text = README.read_text(encoding="utf-8")
        universal = text.split("### Available on all supported devices", 1)[1].split(
            "### Foldable-only", 1
        )[0]
        salami = text.split("### Salami-specific", 1)[1].split(
            "### Build-flavor dependent", 1
        )[0]
        self.assertIn("Full theming", universal)
        self.assertIn("OnePlus Gallery", salami)

    def test_design_uses_approved_settings_location(self) -> None:
        text = (ROOT / "docs" / "design" / "rom-feature-inventory.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Settings → YRRPs → Misc → Features", text)
        self.assertNotIn("YRRP → Misc → Features", text)

    def test_hotspot_inventory_separates_details_from_optional_controls(self) -> None:
        readme = README.read_text(encoding="utf-8")
        design = (ROOT / "docs" / "design" / "rom-feature-inventory.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Connected-client details on all supported devices", readme)
        self.assertIn("Connected-client details remain available", design)
        self.assertIn("Blocking and client-limit controls require", design)
        self.assertIn(
            "A failed hotspot capability probe keeps connected-client details available",
            design,
        )
        self.assertNotIn(
            "Hotspot client management requires the platform client-control capability",
            design,
        )

    def test_project_tracks_no_framework_patch_payloads(self) -> None:
        payloads = (
            []
            if not PATCH_ROOT.exists()
            else [path for path in PATCH_ROOT.rglob("*") if path.is_file()]
        )
        self.assertEqual([], payloads)

    def test_docs_do_not_prescribe_feature_patch_exports(self) -> None:
        candidates = [ROOT / ".claude" / "skills" / "rom-feature-research" / "SKILL.md"]
        candidates.extend((ROOT / "docs" / "design").glob("*.md"))
        forbidden = ("patches/frameworks-base", "Export the series to `patches/")
        hits = []
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            for phrase in forbidden:
                if phrase in text:
                    hits.append(f"{path.relative_to(ROOT)}: {phrase}")
        self.assertEqual([], hits)


if __name__ == "__main__":
    unittest.main()

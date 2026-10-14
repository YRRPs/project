from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PATCH_ROOT = ROOT / "patches" / "frameworks-base"


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
        for feature in (
            "Pulse visualizer",
            "Screen-off effects",
            "Matrix boot and shutdown animation",
            "Fold animations",
            "Ad blocking",
            "Hotspot client management",
            "Indoor auto-brightness tuning",
            "OnePlus Camera integration",
            "GApps integration",
        ):
            self.assertIn(feature, text)

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

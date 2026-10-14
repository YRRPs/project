from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-full-theming-manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_full_theming_manifest", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load full theming manifest checker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VALID = """<manifest>
<remote name="github" fetch=".."/>
<remote name="yrrp" fetch="https://github.com/YRRPs"/>
<remote name="aosp" fetch="https://android.googlesource.com" revision="refs/tags/android-16.0.0_r4"/>
<default revision="refs/heads/lineage-23.2" remote="github"/>
<project path="frameworks/base" name="android_frameworks_base" remote="yrrp"/>
<project path="frameworks/libs/systemui" name="android_frameworks_libs_systemui" remote="yrrp"/>
<project path="packages/apps/ThemePicker" name="android_packages_apps_ThemePicker" remote="yrrp"/>
<project path="external/libmonet" name="platform/external/libmonet" remote="aosp"/>
</manifest>"""


class FullThemingManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def check(self, content: str):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "default.xml"
            path.write_text(content, encoding="utf-8")
            return self.module.check_manifest(path)

    def test_valid_manifest_passes(self) -> None:
        self.assertEqual([], self.check(VALID).errors)

    def test_lineage_theme_picker_is_rejected(self) -> None:
        invalid = VALID.replace(
            'name="android_packages_apps_ThemePicker" remote="yrrp"',
            'name="LineageOS/android_packages_apps_ThemePicker"',
        )
        self.assertIn(
            "packages/apps/ThemePicker must use yrrp/android_packages_apps_ThemePicker",
            self.check(invalid).errors,
        )

    def test_duplicate_path_is_rejected(self) -> None:
        invalid = VALID.replace(
            "</manifest>",
            '<project path="packages/apps/ThemePicker" name="duplicate"/></manifest>',
        )
        self.assertIn("packages/apps/ThemePicker must appear exactly once", self.check(invalid).errors)

    def test_wrong_revision_is_rejected(self) -> None:
        invalid = VALID.replace(
            'path="frameworks/libs/systemui"',
            'path="frameworks/libs/systemui" revision="refs/heads/main"',
        )
        self.assertIn(
            "frameworks/libs/systemui must inherit lineage-23.2 or pin it exactly",
            self.check(invalid).errors,
        )

    def test_libmonet_must_remain_pinned_aosp_project(self) -> None:
        invalid = VALID.replace(
            'name="platform/external/libmonet" remote="aosp"',
            'name="android_external_libmonet" remote="yrrp"',
        )
        self.assertIn("external/libmonet must remain the pinned AOSP project", self.check(invalid).errors)


if __name__ == "__main__":
    unittest.main()

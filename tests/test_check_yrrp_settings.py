from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-yrrp-settings.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_yrrp_settings", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load YRRPs Settings checker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SettingsFixture:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def create_valid(self) -> None:
        homepage = """<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android" xmlns:settings="http://schemas.android.com/apk/res-auto"><PreferenceCategory android:key="yrrp_top_level_category"><Preference android:key="top_level_yrrp" android:title="@string/yrrp_settings_title" android:fragment="com.android.settings.yrrp.YrrpSettings" settings:highlightableMenuKey="@string/yrrp_menu_key"/></PreferenceCategory></PreferenceScreen>"""
        hub = """<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android" xmlns:settings="http://schemas.android.com/apk/res-auto" android:key="yrrp_settings_screen" android:title="@string/yrrp_settings_title"><PreferenceCategory android:key="yrrp_category_test" android:title="@string/yrrp_category_test" settings:searchable="false"><Preference android:key="yrrp_feature_entry" android:title="@string/yrrp_feature_title" android:fragment="com.android.settings.yrrp.YrrpFeatureSettings"/></PreferenceCategory></PreferenceScreen>"""
        feature = """<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android" xmlns:settings="http://schemas.android.com/apk/res-auto" android:key="yrrp_feature_settings_screen" android:title="@string/yrrp_feature_title"><com.android.settingslib.widget.TopIntroPreference android:key="yrrp_feature_intro" android:title="@string/yrrp_feature_intro" settings:searchable="false"/><com.android.settingslib.widget.FooterPreference android:key="yrrp_feature_footer" android:title="@string/yrrp_feature_footer" settings:searchable="false"/></PreferenceScreen>"""
        strings = """<resources><string name="yrrp_settings_title">YRRPs</string><string name="yrrp_menu_key" translatable="false">top_level_yrrp</string><string name="yrrp_category_test">Test</string><string name="yrrp_feature_title">Feature</string><string name="yrrp_feature_intro">Intro</string><string name="yrrp_feature_footer">Footer</string></resources>"""
        self.write("res/xml/top_level_settings.xml", homepage)
        self.write("res/xml/top_level_settings_expressive.xml", homepage)
        self.write("res/xml/yrrp_settings.xml", hub)
        self.write("res/xml/yrrp_feature_settings.xml", feature)
        self.write("res/values/yrrp_strings.xml", strings)
        self.write(
            "src/com/android/settings/yrrp/YrrpSettings.java",
            """package com.android.settings.yrrp; public class YrrpSettings { protected int getPreferenceScreenResId() { return R.xml.yrrp_settings; } }""",
        )
        self.write(
            "src/com/android/settings/yrrp/YrrpFeatureSettings.java",
            """package com.android.settings.yrrp; public class YrrpFeatureSettings { protected int getPreferenceScreenResId() { return R.xml.yrrp_feature_settings; } }""",
        )
        self.write(
            "src/com/android/settings/core/gateway/SettingsGateway.java",
            """class SettingsGateway { String[] entries = {YrrpSettings.class.getName(), YrrpFeatureSettings.class.getName()}; }""",
        )


class CheckYrrpSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = SettingsFixture(self.root)
        self.fixture.create_valid()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_tree_passes_every_check(self) -> None:
        results = self.module.check_settings_tree(self.root)
        self.assertTrue(results)
        self.assertTrue(all(result.passed for result in results), results)

    def test_root_screen_requires_prefixed_key(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                ' android:key="yrrp_feature_settings_screen"', ""
            ),
            encoding="utf-8",
        )

        result = self.result_named("root-screen-keys")

        self.assertFalse(result.passed)
        self.assertIn("yrrp_feature_settings.xml has no android:key", result.detail)

    def test_gateway_contains_every_yrrp_fragment(self) -> None:
        path = self.root / "src/com/android/settings/core/gateway/SettingsGateway.java"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                ", YrrpFeatureSettings.class.getName()", ""
            ),
            encoding="utf-8",
        )

        result = self.result_named("gateway-allowlist")

        self.assertFalse(result.passed)
        self.assertIn("YrrpFeatureSettings", result.detail)

    def test_navigation_title_matches_target_page_title(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'android:title="@string/yrrp_feature_title"',
                'android:title="@string/yrrp_other_title"',
                1,
            ),
            encoding="utf-8",
        )

        result = self.result_named("navigation-title-match")

        self.assertFalse(result.passed)
        self.assertIn("com.android.settings.yrrp.YrrpFeatureSettings", result.detail)
        self.assertIn("@string/yrrp_feature_title", result.detail)
        self.assertIn("@string/yrrp_other_title", result.detail)

    def test_structural_rows_are_not_searchable(self) -> None:
        cases = (
            ("res/xml/yrrp_settings.xml", "PreferenceCategory", "yrrp_category_test"),
            ("res/xml/yrrp_feature_settings.xml", "TopIntroPreference", "yrrp_feature_intro"),
            ("res/xml/yrrp_feature_settings.xml", "FooterPreference", "yrrp_feature_footer"),
        )
        for relative, tag, key in cases:
            with self.subTest(tag=tag):
                self.fixture.create_valid()
                path = self.root / relative
                text = path.read_text(encoding="utf-8")
                marker = f'android:key="{key}"'
                start = text.index(marker)
                searchable_attribute = ' settings:searchable="false"'
                searchable = text.index(searchable_attribute, start)
                path.write_text(
                    text[:searchable] + text[searchable + len(searchable_attribute) :],
                    encoding="utf-8",
                )

                result = self.result_named("structural-searchability")

                self.assertFalse(result.passed)
                self.assertIn(path.name, result.detail)
                self.assertIn(tag, result.detail)
                self.assertIn(key, result.detail)

    def test_highlight_keys_are_unique_within_each_homepage(self) -> None:
        path = self.root / "res/xml/top_level_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "</PreferenceCategory>",
                '<Preference android:key="yrrp_duplicate" settings:highlightableMenuKey="@string/yrrp_menu_key"/></PreferenceCategory>',
            ),
            encoding="utf-8",
        )

        result = self.result_named("homepage-highlight-keys")

        self.assertFalse(result.passed)
        self.assertIn("top_level_settings.xml", result.detail)
        self.assertIn("@string/yrrp_menu_key", result.detail)
        self.assertNotIn("top_level_settings_expressive.xml", result.detail)

    def test_yrrp_tile_exists_in_both_homepages(self) -> None:
        for filename in (
            "top_level_settings.xml",
            "top_level_settings_expressive.xml",
        ):
            with self.subTest(filename=filename):
                self.fixture.create_valid()
                path = self.root / "res/xml" / filename
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        'android:key="top_level_yrrp"', 'android:key="removed"'
                    ),
                    encoding="utf-8",
                )

                result = self.result_named("homepage-yrrp-tile")

                self.assertFalse(result.passed)
                self.assertIn(filename, result.detail)

    def test_yrrp_resources_and_keys_use_prefix(self) -> None:
        xml_path = self.root / "res/xml/yrrp_feature_settings.xml"
        xml_path.write_text(
            xml_path.read_text(encoding="utf-8").replace(
                "</PreferenceScreen>",
                '<Preference android:key="feature_bad"/></PreferenceScreen>',
            ),
            encoding="utf-8",
        )
        strings_path = self.root / "res/values/yrrp_strings.xml"
        strings_path.write_text(
            strings_path.read_text(encoding="utf-8").replace(
                "</resources>", '<string name="feature_bad">Bad</string></resources>'
            ),
            encoding="utf-8",
        )

        result = self.result_named("yrrp-prefixes")

        self.assertFalse(result.passed)
        self.assertIn("feature_bad key", result.detail)
        self.assertIn("feature_bad resource", result.detail)

    def test_list_preference_is_rejected(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "</PreferenceScreen>",
                '<ListPreference android:key="yrrp_bad_list"/></PreferenceScreen>',
            ),
            encoding="utf-8",
        )

        result = self.result_named("no-list-preference")

        self.assertFalse(result.passed)
        self.assertIn("yrrp_feature_settings.xml", result.detail)
        self.assertIn("yrrp_bad_list", result.detail)

    def test_yrrp_arrays_file_is_rejected(self) -> None:
        self.fixture.write("res/values/yrrp_arrays.xml", "<resources/>")

        result = self.result_named("no-yrrp-arrays")

        self.assertFalse(result.passed)
        self.assertIn("yrrp_arrays.xml", result.detail)

    def test_collapsing_group_is_rejected(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "</PreferenceScreen>",
                '<PreferenceCategory android:key="yrrp_bad_group" '
                'settings:initialExpandedChildrenCount="0" settings:searchable="false"/>'
                "</PreferenceScreen>",
            ),
            encoding="utf-8",
        )

        result = self.result_named("no-collapsing-groups")

        self.assertFalse(result.passed)
        self.assertIn("yrrp_feature_settings.xml", result.detail)
        self.assertIn("yrrp_bad_group", result.detail)

    def test_main_returns_zero_and_prints_summary_for_valid_tree(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.module.main([str(self.root)])
        self.assertEqual(0, code)
        self.assertIn("SUMMARY", output.getvalue())
        self.assertIn("0 failed", output.getvalue())

    def test_main_returns_two_for_invalid_root(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = self.module.main([str(self.root / "missing")])
        self.assertEqual(2, code)
        self.assertIn("ERROR invalid Settings root", errors.getvalue())

    def test_main_returns_two_for_malformed_xml(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text("<PreferenceScreen>", encoding="utf-8")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = self.module.main([str(self.root)])
        self.assertEqual(2, code)
        self.assertIn("ERROR invalid Settings tree", errors.getvalue())
        self.assertIn("yrrp_feature_settings.xml", errors.getvalue())

    def test_main_returns_one_for_failed_check(self) -> None:
        path = self.root / "res/xml/yrrp_feature_settings.xml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                ' android:key="yrrp_feature_settings_screen"', ""
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.module.main([str(self.root)])
        self.assertEqual(1, code)
        self.assertIn("FAIL root-screen-keys", output.getvalue())

    def test_multiple_failures_keep_documented_order_and_exact_summary(self) -> None:
        feature = self.root / "res/xml/yrrp_feature_settings.xml"
        feature.write_text(
            feature.read_text(encoding="utf-8").replace(
                ' android:key="yrrp_feature_settings_screen"', ""
            ),
            encoding="utf-8",
        )
        gateway = self.root / "src/com/android/settings/core/gateway/SettingsGateway.java"
        gateway.write_text(
            gateway.read_text(encoding="utf-8").replace(
                ", YrrpFeatureSettings.class.getName()", ""
            ),
            encoding="utf-8",
        )
        self.fixture.write("res/values/yrrp_arrays.xml", "<resources/>")

        results = self.module.check_settings_tree(self.root)
        output = self.module.format_results(results)

        self.assertEqual(
            [
                "root-screen-keys",
                "gateway-allowlist",
                "navigation-title-match",
                "structural-searchability",
                "homepage-highlight-keys",
                "homepage-yrrp-tile",
                "yrrp-prefixes",
                "no-list-preference",
                "no-yrrp-arrays",
                "no-collapsing-groups",
            ],
            [result.name for result in results],
        )
        self.assertEqual(3, sum(not result.passed for result in results))
        self.assertTrue(output.endswith("SUMMARY 7 passed, 3 failed"), output)

    def result_named(self, name: str):
        return next(
            result
            for result in self.module.check_settings_tree(self.root)
            if result.name == name
        )


if __name__ == "__main__":
    unittest.main()

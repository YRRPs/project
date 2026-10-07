#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

DEFAULT_SETTINGS_ROOT = Path("/opt/android/packages/apps/Settings")
ANDROID_NS = "http://schemas.android.com/apk/res/android"
SETTINGS_NS = "http://schemas.android.com/apk/res-auto"
ANDROID = f"{{{ANDROID_NS}}}"
SETTINGS = f"{{{SETTINGS_NS}}}"
APPROVED_NON_YRRP_KEYS = frozenset({"top_level_yrrp"})


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(".", 1)[-1]


def _read_xml(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"{path}: {error}") from error


def _yrrp_screens(root: Path) -> list[tuple[Path, ET.Element]]:
    screens = []
    for path in sorted((root / "res/xml").glob("yrrp*.xml")):
        element = _read_xml(path)
        if _local_name(element.tag) == "PreferenceScreen":
            screens.append((path, element))
    return screens


def _fragment_xml_map(root: Path) -> dict[str, str]:
    mapping = {}
    source_dir = root / "src/com/android/settings/yrrp"
    for path in sorted(source_dir.glob("Yrrp*Settings.java")):
        text = path.read_text(encoding="utf-8")
        class_match = re.search(r"public class (Yrrp\w*Settings)\b", text)
        xml_match = re.search(r"return R\.xml\.(yrrp_\w+);", text)
        if class_match and xml_match:
            mapping[f"com.android.settings.yrrp.{class_match.group(1)}"] = (
                xml_match.group(1)
            )
    return mapping


def _gateway_fragments(root: Path) -> set[str]:
    path = root / "src/com/android/settings/core/gateway/SettingsGateway.java"
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\b(Yrrp\w*Settings)\.class\.getName\(\)", text))


def _homepage_roots(root: Path) -> list[tuple[Path, ET.Element]]:
    names = ("top_level_settings.xml", "top_level_settings_expressive.xml")
    return [
        (root / "res/xml" / name, _read_xml(root / "res/xml" / name))
        for name in names
    ]


def _check_root_screen_keys(root: Path) -> CheckResult:
    failures = []
    screens = _yrrp_screens(root)
    for path, element in screens:
        key = element.get(f"{ANDROID}key")
        if not key:
            failures.append(f"{path.name} has no android:key")
        elif not key.startswith("yrrp_"):
            failures.append(f"{path.name} root key {key!r} does not start with yrrp_")
    detail = "; ".join(failures) if failures else f"{len(screens)} screens checked"
    return CheckResult("root-screen-keys", not failures, detail)


def _check_gateway_allowlist(root: Path) -> CheckResult:
    fragment_map = _fragment_xml_map(root)
    required = {fragment.rsplit(".", 1)[-1] for fragment in fragment_map}
    for _, screen in [*_yrrp_screens(root), *_homepage_roots(root)]:
        for element in screen.iter():
            fragment = element.get(f"{ANDROID}fragment", "")
            if fragment.startswith("com.android.settings.yrrp."):
                required.add(fragment.rsplit(".", 1)[-1])
    missing = sorted(required - _gateway_fragments(root))
    detail = (
        "missing " + ", ".join(missing)
        if missing
        else f"{len(required)} fragments present"
    )
    return CheckResult("gateway-allowlist", not missing, detail)


def _check_navigation_titles(root: Path) -> CheckResult:
    fragment_map = _fragment_xml_map(root)
    screens = {path.stem: element for path, element in _yrrp_screens(root)}
    failures = []
    for path, source in [*_yrrp_screens(root), *_homepage_roots(root)]:
        for element in source.iter():
            fragment = element.get(f"{ANDROID}fragment", "")
            if not fragment.startswith("com.android.settings.yrrp."):
                continue
            resource = fragment_map.get(fragment)
            target = screens.get(resource) if resource else None
            if target is None:
                failures.append(f"{path.name}: {fragment} has no mapped target XML")
                continue
            source_title = element.get(f"{ANDROID}title", "")
            target_title = target.get(f"{ANDROID}title", "")
            if source_title != target_title:
                failures.append(
                    f"{fragment}: {source_title!r} does not match {target_title!r}"
                )
    detail = "; ".join(failures) if failures else "all navigation titles match"
    return CheckResult("navigation-title-match", not failures, detail)


def _check_structural_searchability(root: Path) -> CheckResult:
    structural_tags = {"PreferenceCategory", "TopIntroPreference", "FooterPreference"}
    failures = []
    for path, screen in _yrrp_screens(root):
        for element in screen.iter():
            tag = _local_name(element.tag)
            if tag not in structural_tags:
                continue
            if element.get(f"{SETTINGS}searchable") == "false":
                continue
            key = element.get(f"{ANDROID}key", "<no key>")
            failures.append(f"{path.name}: {tag} {key} must set searchable=false")
    detail = "; ".join(failures) if failures else "all structural rows excluded"
    return CheckResult("structural-searchability", not failures, detail)


def _check_homepage_highlight_keys(root: Path) -> CheckResult:
    failures = []
    for path, homepage in _homepage_roots(root):
        seen = set()
        duplicates = set()
        for element in homepage.iter():
            key = element.get(f"{SETTINGS}highlightableMenuKey")
            if not key:
                continue
            if key in seen:
                duplicates.add(key)
            seen.add(key)
        if duplicates:
            failures.append(f"{path.name}: duplicate {', '.join(sorted(duplicates))}")
    detail = "; ".join(failures) if failures else "highlight keys unique per homepage"
    return CheckResult("homepage-highlight-keys", not failures, detail)


def _check_homepage_yrrp_tile(root: Path) -> CheckResult:
    missing = []
    for path, homepage in _homepage_roots(root):
        keys = {element.get(f"{ANDROID}key") for element in homepage.iter()}
        if "top_level_yrrp" not in keys:
            missing.append(path.name)
    detail = (
        "missing top_level_yrrp in " + ", ".join(missing)
        if missing
        else "top_level_yrrp present in both homepages"
    )
    return CheckResult("homepage-yrrp-tile", not missing, detail)


def _check_yrrp_prefixes(root: Path) -> CheckResult:
    failures = []
    for path, screen in _yrrp_screens(root):
        for element in screen.iter():
            key = element.get(f"{ANDROID}key")
            if key and not key.startswith("yrrp_") and key not in APPROVED_NON_YRRP_KEYS:
                failures.append(f"{path.name}: {key} key lacks yrrp_ prefix")
    for path in sorted((root / "res/values").glob("yrrp_*.xml")):
        for element in _read_xml(path):
            name = element.get("name")
            if name and not name.startswith("yrrp_"):
                failures.append(f"{path.name}: {name} resource lacks yrrp_ prefix")
    detail = "; ".join(failures) if failures else "all keys and resources prefixed"
    return CheckResult("yrrp-prefixes", not failures, detail)


def _check_no_list_preference(root: Path) -> CheckResult:
    failures = []
    for path, screen in _yrrp_screens(root):
        for element in screen.iter():
            if _local_name(element.tag) != "ListPreference":
                continue
            key = element.get(f"{ANDROID}key", "<no key>")
            failures.append(f"{path.name}: ListPreference {key}")
    detail = "; ".join(failures) if failures else "no YRRPs ListPreference"
    return CheckResult("no-list-preference", not failures, detail)


def _check_no_yrrp_arrays(root: Path) -> CheckResult:
    path = root / "res/values/yrrp_arrays.xml"
    return CheckResult(
        "no-yrrp-arrays",
        not path.exists(),
        "yrrp_arrays.xml is forbidden" if path.exists() else "no yrrp_arrays.xml",
    )


def check_settings_tree(root: Path) -> list[CheckResult]:
    """Return every structural check in deterministic order."""
    return [
        _check_root_screen_keys(root),
        _check_gateway_allowlist(root),
        _check_navigation_titles(root),
        _check_structural_searchability(root),
        _check_homepage_highlight_keys(root),
        _check_homepage_yrrp_tile(root),
        _check_yrrp_prefixes(root),
        _check_no_list_preference(root),
        _check_no_yrrp_arrays(root),
    ]


def format_results(results: list[CheckResult]) -> str:
    """Render PASS/FAIL lines and final summary."""
    lines = [
        f"{'PASS' if result.passed else 'FAIL'} {result.name}: {result.detail}"
        for result in results
    ]
    passed = sum(result.passed for result in results)
    failed = len(results) - passed
    lines.append(f"SUMMARY {passed} passed, {failed} failed")
    return "\n".join(lines)


def _is_settings_root(root: Path) -> bool:
    required = (
        root / "res/xml",
        root / "src/com/android/settings/core/gateway/SettingsGateway.java",
        root / "src/com/android/settings/yrrp",
    )
    return required[0].is_dir() and required[1].is_file() and required[2].is_dir()


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when all checks pass, 1 for violations, 2 for invalid input."""
    parser = argparse.ArgumentParser(description="Check YRRPs Settings structure")
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_SETTINGS_ROOT)
    arguments = parser.parse_args(argv)
    root = arguments.root
    if not _is_settings_root(root):
        print(f"ERROR invalid Settings root: {root}", file=sys.stderr)
        return 2
    try:
        results = check_settings_tree(root)
    except (OSError, ValueError) as error:
        print(f"ERROR invalid Settings tree: {error}", file=sys.stderr)
        return 2
    print(format_results(results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

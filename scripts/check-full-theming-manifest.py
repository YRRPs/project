#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

TARGET_REVISION = "refs/heads/lineage-23.2"
EXPECTED = {
    "frameworks/base": "android_frameworks_base",
    "frameworks/libs/systemui": "android_frameworks_libs_systemui",
    "packages/apps/ThemePicker": "android_packages_apps_ThemePicker",
}


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]

    @property
    def passed(self) -> bool:
        return not self.errors


def check_manifest(path: Path) -> CheckResult:
    root = ET.parse(path).getroot()
    errors: list[str] = []
    default = root.find("default")
    inherited_revision = default.get("revision") if default is not None else None

    for project_path, expected_name in EXPECTED.items():
        projects = root.findall(f"project[@path='{project_path}']")
        if len(projects) != 1:
            errors.append(f"{project_path} must appear exactly once")
            continue
        project = projects[0]
        if project.get("remote") != "yrrp" or project.get("name") != expected_name:
            errors.append(f"{project_path} must use yrrp/{expected_name}")
        revision = project.get("revision", inherited_revision)
        if revision != TARGET_REVISION:
            errors.append(f"{project_path} must inherit lineage-23.2 or pin it exactly")

    libmonet = root.findall("project[@path='external/libmonet']")
    aosp = root.find("remote[@name='aosp']")
    if (
        len(libmonet) != 1
        or libmonet[0].get("name") != "platform/external/libmonet"
        or libmonet[0].get("remote") != "aosp"
        or aosp is None
        or aosp.get("revision") != "refs/tags/android-16.0.0_r4"
    ):
        errors.append("external/libmonet must remain the pinned AOSP project")

    return CheckResult(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check full theming manifest topology")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        result = check_manifest(args.manifest)
    except (OSError, ET.ParseError) as error:
        print(error, file=sys.stderr)
        return 2
    if result.passed:
        print("full theming manifest: PASS")
        return 0
    for error in result.errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

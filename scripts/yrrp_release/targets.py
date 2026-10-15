from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Executor = Literal["m", "atest"]
SUPPORTED_DEVICES = ("salami", "xueying")


@dataclass(frozen=True)
class Target:
    id: str
    executor: Executor
    argument: str
    gating: bool = True


_TARGET_VALUES = (
    Target("atest:SettingsProviderTest", "atest", "SettingsProviderTest"),
    Target("atest:SystemUITests_presubmit", "atest", "SystemUITests_presubmit"),
    Target("atest:SystemUiRoboTests", "atest", "SystemUiRoboTests", gating=False),
    Target("atest:ThemeOverlayApplierTest", "atest", "ThemeOverlayApplierTest"),
    Target("atest:ThemePickerRoboTests", "atest", "ThemePickerRoboTests", gating=False),
    Target("atest:monet_tests", "atest", "monet_tests"),
    Target("m:Settings", "m", "Settings"),
    Target("m:SettingsProvider", "m", "SettingsProvider"),
    Target("m:SettingsRoboTests", "m", "SettingsRoboTests"),
    Target("m:SystemUI", "m", "SystemUI"),
    Target("m:SystemUI-core", "m", "SystemUI-core"),
    Target("m:ThemePicker", "m", "ThemePicker"),
    Target("m:monet", "m", "monet"),
)
TARGETS = MappingProxyType({target.id: target for target in _TARGET_VALUES})


def list_targets() -> dict[str, list[str]]:
    return {
        "devices": list(SUPPORTED_DEVICES),
        "targets": list(TARGETS),
        "non_gating_targets": [
            target.id for target in _TARGET_VALUES if not target.gating
        ],
    }


def parse_gating_targets(values: list[str]) -> tuple[Target, ...]:
    parsed = parse_targets(values)
    non_gating = [target.id for target in parsed if not target.gating]
    if non_gating:
        raise ValueError(
            "non-gating targets cannot satisfy release preparation: "
            + ", ".join(non_gating)
        )
    return parsed


def validate_device(value: str) -> str:
    if value not in SUPPORTED_DEVICES:
        raise ValueError(f"unsupported device: {value!r}")
    return value


def parse_targets(values: list[str]) -> tuple[Target, ...]:
    if not values:
        raise ValueError("at least one target is required")
    parsed: list[Target] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate target: {value}")
        try:
            target = TARGETS[value]
        except KeyError as error:
            raise ValueError(f"unsupported target: {value!r}") from error
        seen.add(value)
        parsed.append(target)
    return tuple(parsed)

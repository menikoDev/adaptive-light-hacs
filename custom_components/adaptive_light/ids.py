"""Entity ids the integration creates."""
from __future__ import annotations

from .model import Config


def expected_entities(cfg: Config) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"binary_sensor": [], "switch": [], "sensor": []}
    for zone in cfg.zone_order:
        out["binary_sensor"].append(f"binary_sensor.{zone}_occupied")
        out["switch"].append(f"switch.{zone}_enabled")
        out["sensor"].append(f"sensor.{zone}_estimate")
    return out

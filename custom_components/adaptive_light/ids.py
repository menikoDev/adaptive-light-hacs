"""Entity ids the integration creates. Pure; used by the platforms, the dashboard generator and the tests.

The first (primary) zone keeps the ids of the YAML plan (``sensor.light_need``, ``sensor.living_room_ambient_lux`` ...);
every other zone gets its zone key as a prefix (``sensor.bedroom_light_need``). Lamp entities are named by the lamp key.
"""
from __future__ import annotations

from .model import Config

ZONE_SENSORS = (  # (suffix template, value key, name, unit)
    ("{z}_ambient_lux", "ambient", "ambient lux", "lx"),
    ("{p}light_need", "need", "light need", "%"),
    ("{p}light_ambient_trust", "trust", "light ambient trust", "%"),
    ("{p}light_openness", "openness", "light openness", "%"),
    ("{p}light_openness_raw", "openness_raw", "light openness raw", "%"),
)


def prefix(cfg: Config, zone: str) -> str:
    return "" if zone == cfg.primary_zone else f"{zone}_"


def zone_sensor_ids(cfg: Config, zone: str) -> dict[str, str]:
    """value key -> object id of the zone's sensors."""
    return {key: tmpl.format(z=zone, p=prefix(cfg, zone)) for tmpl, key, _, _ in ZONE_SENSORS}


def expected_entities(cfg: Config) -> dict[str, list[str]]:
    """platform -> entity ids for a config."""
    out: dict[str, list[str]] = {"sensor": ["sensor.daylight_factor"], "binary_sensor": [], "switch": [
        "switch.light_adaptive_enabled", "switch.light_adaptive_apply", "switch.light_adaptive_scenarios"], "number": []}
    for z in cfg.zone_order:
        out["sensor"] += [f"sensor.{o}" for o in zone_sensor_ids(cfg, z).values()]
        out["binary_sensor"].append(f"binary_sensor.{prefix(cfg, z)}light_evening_quiet")
    for k in cfg.lamps:
        out["sensor"] += [f"sensor.light_target_{k}", f"sensor.light_{k}_pause_until"]
        out["switch"].append(f"switch.light_{k}_auto")
        out["number"].append(f"number.light_k_{k}")
    return out

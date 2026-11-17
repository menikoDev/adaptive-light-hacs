"""Configuration: zones only.

A zone has a name, one or more lamps, one or more presence sensors, and an off timeout
in seconds. Night mode is optional: during its window, an hour without new movement
turns the lamps off even if a presence sensor stays on. Older shapes are dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CONFIG_VERSION = 5
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def empty_config() -> dict:
    return {"version": CONFIG_VERSION, "zones": []}


class ConfigError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _timeout(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    number = int(value)
    if number < 0:
        return None
    return number


def _clock(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    parts = value.strip().split(":")
    if len(parts) < 2:
        return default
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _factor(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value <= 0:
        return default
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _still(value: Any) -> int:
    number = _timeout(value)
    if number is None or number < 1:
        return 60
    return min(number, 24 * 60)


def _entities(value: Any, domain: str) -> list[str] | None:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        return None
    prefix = domain + "."
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.startswith(prefix) or item in out:
            return None
        out.append(item)
    return out


def _zone(raw: Any, used: set[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key, name = raw.get("key"), raw.get("name")
    if not isinstance(key, str) or not KEY_RE.match(key) or key in used:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    lamps = _entities(raw.get("lamps"), "light")
    presence = _entities(raw.get("presence"), "binary_sensor")
    timeout = _timeout(raw.get("off_timeout"))
    if lamps is None or presence is None or timeout is None:
        return None
    lux = raw.get("lux") or ""
    if not isinstance(lux, str) or (lux and not lux.startswith("sensor.")):
        lux = ""
    threshold = _timeout(raw.get("threshold"))
    if threshold is None:
        threshold = 50
    generation = raw.get("manual_clear")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        generation = 0
    return {
        "key": key, "name": name.strip(), "lamps": lamps, "presence": presence, "off_timeout": timeout,
        "lux": lux, "threshold": threshold, "lux_coefficient": _factor(raw.get("lux_coefficient"), 1),
        "manual_clear": generation,
        "night": raw.get("night") is True,
        "night_start": _clock(raw.get("night_start"), "02:00"),
        "night_end": _clock(raw.get("night_end"), "06:00"),
        "night_still": _still(raw.get("night_still")),
    }


def outdoor_block(raw: Any) -> dict:
    """Controller-wide outdoor light. An empty radiation sensor means zones without a lux sensor stay on presence only."""
    if not isinstance(raw, dict):
        raw = {}
    radiation = raw.get("radiation") or ""
    if not isinstance(radiation, str) or (radiation and not radiation.startswith("sensor.")):
        radiation = ""
    return {"radiation": radiation, "factor": _factor(raw.get("factor"), 0.6)}


def normalise(raw: Any) -> dict:
    """Keep only zones that have the four fields. Older shapes are dropped, not converted."""
    out = empty_config()
    if not isinstance(raw, dict) or not isinstance(raw.get("zones"), list):
        return out
    used: set[str] = set()
    for item in raw["zones"]:
        zone = _zone(item, used)
        if zone is None:
            continue
        used.add(zone["key"])
        out["zones"].append(zone)
    if isinstance(raw.get("outdoor"), dict):
        out["outdoor"] = outdoor_block(raw["outdoor"])
    return out


def validate(cfg: dict) -> list[ConfigError]:
    errors: list[ConfigError] = []
    if not cfg.get("zones"):
        errors.append(ConfigError("need_zone"))
    seen: set[str] = set()
    for zone in cfg.get("zones") or []:
        if zone["key"] in seen:
            errors.append(ConfigError("duplicate_key", zone["key"]))
        seen.add(zone["key"])
        if not zone["lamps"]:
            errors.append(ConfigError("need_lamps", zone["name"]))
        if not zone["presence"]:
            errors.append(ConfigError("need_presence", zone["name"]))
        if not isinstance(zone["off_timeout"], int) or zone["off_timeout"] < 0:
            errors.append(ConfigError("bad_timeout", zone["name"]))
    return errors


def unique_key(base: str, used: set[str]) -> str:
    base = base if isinstance(base, str) and KEY_RE.match(base) else "zone"
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def upsert_zone(cfg: dict, zone: dict) -> None:
    zones = []
    replaced = False
    for current in cfg.get("zones") or []:
        if current["key"] == zone["key"]:
            zones.append(zone)
            replaced = True
        else:
            zones.append(current)
    if not replaced:
        zones.append(zone)
    cfg["zones"] = zones


@dataclass
class Config:
    version: int
    zones: dict[str, dict]
    zone_order: list[str]
    outdoor: dict | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "Config":
        data = normalise(raw)
        zones = {z["key"]: z for z in data["zones"]}
        return cls(CONFIG_VERSION, zones, [z["key"] for z in data["zones"]], data.get("outdoor"))

    def to_dict(self) -> dict:
        out = {"version": CONFIG_VERSION, "zones": [self.zones[k] for k in self.zone_order]}
        if self.outdoor is not None:
            out["outdoor"] = dict(self.outdoor)
        return out

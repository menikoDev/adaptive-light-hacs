"""Configuration: zones only.

A zone has a name, one or more lamps, one or more presence sensors, and an off timeout
in seconds. Anything else in an older config (rules, groups, coefficients, sensors) is dropped.
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
    return {"key": key, "name": name.strip(), "lamps": lamps, "presence": presence, "off_timeout": timeout}


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

    @classmethod
    def from_dict(cls, raw: Any) -> "Config":
        data = normalise(raw)
        zones = {z["key"]: z for z in data["zones"]}
        return cls(CONFIG_VERSION, zones, [z["key"] for z in data["zones"]])

    def to_dict(self) -> dict:
        return {"version": CONFIG_VERSION, "zones": [self.zones[k] for k in self.zone_order]}

"""Pure edit operations on a config dict, shared by the setup wizard and the options flow (and the tests)."""
from __future__ import annotations

from . import params as P
from .model import ConfigError, slug


def _unique(base: str, taken) -> str:
    key, n = slug(base), 2
    while key in taken:
        key = f"{slug(base)[:36]}_{n}"
        n += 1
    return key


unique_key = _unique


def find(cfg: dict, coll: str, key: str) -> dict:
    for x in cfg[coll]:
        if x["key"] == key:
            return x
    raise ConfigError("unknown_" + coll.rstrip("s"), key)


def add_zone(cfg: dict, name: str, key: str | None = None, area_id: str | None = None) -> str:
    """Add a zone. ``key`` (a prefilled or typed zone key) defaults to the slug of the name; it is made unique.
    ``area_id`` remembers the Home Assistant Area the zone was created from."""
    key = _unique(key or name, {z["key"] for z in cfg["zones"]})
    zone = {"key": key, "name": name.strip() or key, "params": {}}
    if area_id:
        zone["area_id"] = area_id
    cfg["zones"].append(zone)
    return key


def set_sensor(cfg: dict, role: str, entity: str | None, zone: str = "", *, replace: bool = True) -> None:
    """Set (or clear with entity=None) the sensor of a role in a zone. replace=False appends (several media sensors)."""
    if replace:
        cfg["sensors"] = [s for s in cfg["sensors"] if not (s["role"] == role and s["zone"] == zone)]
    if entity:
        if not any(s["role"] == role and s["zone"] == zone and s["entity"] == entity for s in cfg["sensors"]):
            cfg["sensors"].append({"entity": entity, "role": role, "zone": zone})


def as_entities(value) -> list[str]:
    """A form value that is one entity id or a list of them."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [e for e in value if e]


def set_sensors(cfg: dict, role: str, entities, zone: str = "") -> None:
    """Replace every sensor of ``role`` in ``zone`` with ``entities`` (order kept, duplicates dropped)."""
    cfg["sensors"] = [s for s in cfg["sensors"] if not (s["role"] == role and s["zone"] == zone)]
    for e in as_entities(entities):
        if not any(s["role"] == role and s["zone"] == zone and s["entity"] == e for s in cfg["sensors"]):
            cfg["sensors"].append({"entity": e, "role": role, "zone": zone})


def set_presence(cfg: dict, zone: str, entities) -> None:
    """Presence sensors of a zone. One sensor stays one sensor; several are allowed. The zone is occupied if any is on."""
    set_sensors(cfg, "presence", entities, zone)


def set_media(cfg: dict, zone: str, entities: list[str]) -> None:
    set_sensor(cfg, "media", None, zone)
    for e in entities:
        set_sensor(cfg, "media", e, zone, replace=False)


def add_lamps(cfg: dict, zone: str, entities: list[str]) -> list[str]:
    """Add lamps from light entity ids; key = slug of the object id. Already known entities are skipped."""
    have = {x["entity"] for x in cfg["lamps"]}
    keys = []
    for e in entities:
        if e in have:
            continue
        key = _unique(e.split(".", 1)[1], {x["key"] for x in cfg["lamps"]})
        cfg["lamps"].append({"key": key, "entity": e, "zone": zone, "name": key, "params": {}})
        keys.append(key)
    return keys


def remove_lamp(cfg: dict, key: str) -> None:
    cfg["lamps"] = [x for x in cfg["lamps"] if x["key"] != key]
    for g in cfg["groups"]:
        g["lamps"] = [k for k in g["lamps"] if k != key]


def add_group(cfg: dict, zone: str, name: str, lamps: list[str], share_pause: bool = False) -> str:
    key = _unique(name, {g["key"] for g in cfg["groups"]})
    cfg["groups"].append({"key": key, "name": name.strip() or key, "zone": zone, "lamps": list(lamps), "params": {},
                          "share_pause": share_pause})
    return key


def remove_group(cfg: dict, key: str) -> None:
    cfg["groups"] = [g for g in cfg["groups"] if g["key"] != key]
    cfg["rules"] = [r for r in cfg.get("rules") or [] if r.get("group") != key]


def remove_zone(cfg: dict, key: str) -> None:
    for x in [x["key"] for x in cfg["lamps"] if x["zone"] == key]:
        remove_lamp(cfg, x)
    cfg["groups"] = [g for g in cfg["groups"] if g["zone"] != key]
    cfg["sensors"] = [s for s in cfg["sensors"] if s["zone"] != key]
    cfg["zones"] = [z for z in cfg["zones"] if z["key"] != key]
    cfg["rules"] = [r for r in cfg.get("rules") or [] if r.get("zone") != key]


def holder(cfg: dict, level: str, target: str = "") -> dict:
    return {"global": lambda: cfg, "zone": lambda: find(cfg, "zones", target), "group": lambda: find(cfg, "groups", target),
            "lamp": lambda: find(cfg, "lamps", target)}[level]()


def set_overrides(cfg: dict, level: str, target: str, values: dict) -> None:
    """Replace the whole set of overrides at a level; names missing from ``values`` mean 'inherit'."""
    holder(cfg, level, target)["params"] = P.clean_overrides(values, level)


def overrides(cfg: dict, level: str, target: str = "") -> dict:
    return dict(holder(cfg, level, target).get("params", {}))



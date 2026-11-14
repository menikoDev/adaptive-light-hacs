"""Pure edit operations on a config dict, shared by the setup wizard and the options flow (and the tests)."""
from __future__ import annotations

from . import params as P
from .executors import FRIDGE_KELVIN, FRIDGE_LEAVE_S, FRIDGE_PCT
from .model import BINDING_OFF, ZONE_PRESENCE, ConfigError, slug

# Starter template "room presence -> main group, adaptive, off after leave": 10 minutes, the same
# grace the leave-the-room scenarios already use. The form shows the number; it is not applied on its own.
ROOM_OFF_DELAY_S = 600


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
    zone = {"key": key, "name": name.strip() or key, "params": {}, "bindings": {}}
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
    for z in cfg["zones"]:
        z["bindings"] = {s: v for s, v in z.get("bindings", {}).items() if v != key}
        if z.get("detect_rules"):
            kept = []
            for rule in z["detect_rules"]:
                lamps = [k for k in rule.get("lamps") or [] if k != key]
                if lamps:
                    rule = dict(rule)
                    rule["lamps"] = lamps
                    kept.append(rule)
            z["detect_rules"] = kept


def add_group(cfg: dict, zone: str, name: str, lamps: list[str], share_pause: bool = False) -> str:
    key = _unique(name, {g["key"] for g in cfg["groups"]})
    cfg["groups"].append({"key": key, "name": name.strip() or key, "zone": zone, "lamps": list(lamps), "params": {},
                          "share_pause": share_pause})
    return key


def remove_group(cfg: dict, key: str) -> None:
    cfg["groups"] = [g for g in cfg["groups"] if g["key"] != key]
    for z in cfg["zones"]:
        z["bindings"] = {s: v for s, v in z.get("bindings", {}).items() if v != key}


def remove_zone(cfg: dict, key: str) -> None:
    for x in [x["key"] for x in cfg["lamps"] if x["zone"] == key]:
        remove_lamp(cfg, x)
    cfg["groups"] = [g for g in cfg["groups"] if g["zone"] != key]
    cfg["sensors"] = [s for s in cfg["sensors"] if s["zone"] != key]
    cfg["zones"] = [z for z in cfg["zones"] if z["key"] != key]


def holder(cfg: dict, level: str, target: str = "") -> dict:
    return {"global": lambda: cfg, "zone": lambda: find(cfg, "zones", target), "group": lambda: find(cfg, "groups", target),
            "lamp": lambda: find(cfg, "lamps", target)}[level]()


def set_overrides(cfg: dict, level: str, target: str, values: dict) -> None:
    """Replace the whole set of overrides at a level; names missing from ``values`` mean 'inherit'."""
    holder(cfg, level, target)["params"] = P.clean_overrides(values, level)


def overrides(cfg: dict, level: str, target: str = "") -> dict:
    return dict(holder(cfg, level, target).get("params", {}))


def bindings_from_form(user_input: dict) -> dict:
    """Stored bindings. An explicit Off and an empty field both remove the slot, so the scenario stays off."""
    return {k: v for k, v in (user_input or {}).items() if v and v != BINDING_OFF}


def _rule_id(rules: list[dict]) -> str:
    have = {r.get("id") for r in rules}
    n = 1
    while f"d{n}" in have:
        n += 1
    return f"d{n}"


def add_detect_rule(cfg: dict, zone: str, *, sensor: str, lamps: list[str], brightness: str, off_delay_s: int,
                    brightness_pct: int | None = None, color_temp_kelvin: int | None = None) -> str:
    z = find(cfg, "zones", zone)
    rules = z.setdefault("detect_rules", [])
    rid = _rule_id(rules)
    rule: dict = {"id": rid, "sensor": sensor, "lamps": list(dict.fromkeys(lamps)), "brightness": brightness,
                  "off_delay_s": int(off_delay_s)}
    if brightness == "fixed":
        rule["brightness_pct"] = int(brightness_pct)
        if color_temp_kelvin not in (None, ""):
            rule["color_temp_kelvin"] = int(color_temp_kelvin)
    rules.append(rule)
    return rid


def delete_detect_rule(cfg: dict, zone: str, rule_id: str) -> None:
    z = find(cfg, "zones", zone)
    z["detect_rules"] = [r for r in z.get("detect_rules") or [] if r.get("id") != rule_id]


def main_group_lamps(cfg: dict, zone: str) -> list[str]:
    """Lamps of the main group: the top-lights binding, else the first group of the zone, else every lamp."""
    z = find(cfg, "zones", zone)
    bound = (z.get("bindings") or {}).get("top_group")
    if bound:
        for g in cfg["groups"]:
            if g["key"] == bound and g.get("zone") == zone and g.get("lamps"):
                return list(g["lamps"])
    for g in cfg["groups"]:
        if g.get("zone") == zone and g.get("lamps"):
            return list(g["lamps"])
    return [x["key"] for x in cfg["lamps"] if x.get("zone") == zone]


def room_template(cfg: dict, zone: str) -> dict | None:
    """Optional starter: combined room presence turns the main group on (adaptive) and off 10 min after it clears."""
    lamps = main_group_lamps(cfg, zone)
    if not lamps:
        return None
    return {"sensor": ZONE_PRESENCE, "lamps": lamps, "brightness": "adaptive", "off_delay_s": ROOM_OFF_DELAY_S}


def fridge_template(cfg: dict, zone: str) -> dict | None:
    """Optional starter: the fridge sensor, one lamp, fixed 30% at 4500 K, off 10 s after it clears.

    The lamp is the fridge binding when that slot is set, otherwise the first lamp of the zone.
    Returns None when the zone has no fridge sensor or no lamp: the template is never forced."""
    sensor = next((s["entity"] for s in cfg["sensors"] if s.get("role") == "fridge" and s.get("zone") == zone), None)
    if not sensor:
        return None
    z = find(cfg, "zones", zone)
    lamp = (z.get("bindings") or {}).get("fridge_lamp")
    if lamp not in {x["key"] for x in cfg["lamps"] if x.get("zone") == zone}:
        lamps = [x["key"] for x in cfg["lamps"] if x.get("zone") == zone]
        lamp = lamps[0] if lamps else None
    if not lamp:
        return None
    return {"sensor": sensor, "lamps": [lamp], "brightness": "fixed", "brightness_pct": FRIDGE_PCT,
            "color_temp_kelvin": FRIDGE_KELVIN, "off_delay_s": FRIDGE_LEAVE_S}

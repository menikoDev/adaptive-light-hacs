"""Configuration model: zones, sensors (with role and zone), lamps, groups, parameter inheritance.

Pure data + validation, no Home Assistant imports. The config flow edits exactly this structure;
the runtime reads it through ``Config``.

    {
      "version": 2,
      "params":  {name: value},                                  # global overrides
      "sensors": [{"entity", "role", "zone"}],                   # zone "" = global (radiation, elevation)
      "zones":   [{"key", "name", "params", "bindings"}],        # one per room, the first is the primary one
      "lamps":   [{"key", "entity", "zone", "name", "params"}],  # any number
      "groups":  [{"key", "name", "zone", "lamps": [keys], "params", "share_pause"}]
    }
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from . import params as P

CONFIG_VERSION = 2
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

GLOBAL_ROLES = ("radiation", "elevation")
ZONE_ROLES = ("illuminance", "presence", "media", "sofa", "table", "fridge", "entrance", "carpet",
              "window_advice", "window_left", "window_right", "vent_mode", "color_store")
ROLES = GLOBAL_ROLES + ZONE_ROLES
ROLE_DOMAINS = {
    "illuminance": ("sensor",), "presence": ("binary_sensor",), "radiation": ("sensor",), "elevation": ("sensor",),
    "media": ("media_player", "binary_sensor", "switch", "remote"),
    "sofa": ("binary_sensor",), "table": ("binary_sensor",), "fridge": ("binary_sensor",), "entrance": ("binary_sensor",),
    "carpet": ("binary_sensor",), "window_advice": ("sensor",), "window_left": ("sensor",), "window_right": ("sensor",),
    "vent_mode": ("input_boolean", "switch", "binary_sensor"), "color_store": ("input_text",),
}
BINDING_SLOTS = {            # slot -> kind of key it takes
    "top_group": "group", "desk_group": "group", "wake_group": "group",
    "fridge_lamp": "lamp", "vent_left": "lamp", "vent_right": "lamp", "green_lamp": "lamp",
}


def empty_config() -> dict:
    return {"version": CONFIG_VERSION, "params": {}, "sensors": [], "zones": [], "lamps": [], "groups": []}


class ConfigError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "x_" + s if s else "x"
    return s[:41]


def validate(cfg: dict) -> list[ConfigError]:
    """All problems of a config; empty list = usable. Codes are translation keys of the config flow."""
    errs: list[ConfigError] = []
    zones = {z["key"] for z in cfg.get("zones", [])}
    zlist = [z["key"] for z in cfg.get("zones", [])]
    llist = [x["key"] for x in cfg.get("lamps", [])]
    glist = [x["key"] for x in cfg.get("groups", [])]
    for kind, keys in (("zone", zlist), ("lamp", llist), ("group", glist)):
        for k in keys:
            if not KEY_RE.match(k or ""):
                errs.append(ConfigError("bad_key", f"{kind} {k!r}"))
        if len(set(keys)) != len(keys):
            errs.append(ConfigError("duplicate_key", kind))
    if set(llist) & set(glist):
        errs.append(ConfigError("duplicate_key", "lamp and group share a key"))
    ents = [x["entity"] for x in cfg.get("lamps", [])]
    if len(set(ents)) != len(ents):
        errs.append(ConfigError("duplicate_lamp_entity"))
    lamp_zone = {x["key"]: x.get("zone") for x in cfg.get("lamps", [])}
    for x in cfg.get("lamps", []):
        if x.get("zone") not in zones:
            errs.append(ConfigError("unknown_zone", f"lamp {x['key']}"))
    for g in cfg.get("groups", []):
        if g.get("zone") not in zones:
            errs.append(ConfigError("unknown_zone", f"group {g['key']}"))
        for lk in g.get("lamps", []):
            if lk not in lamp_zone:
                errs.append(ConfigError("unknown_lamp", f"group {g['key']}: {lk}"))
            elif lamp_zone[lk] != g.get("zone"):
                errs.append(ConfigError("lamp_zone_mismatch", f"group {g['key']}: {lk}"))
    for s in cfg.get("sensors", []):
        role, z = s.get("role"), s.get("zone", "")
        if role not in ROLES:
            errs.append(ConfigError("bad_role", str(role)))
            continue
        if (role in GLOBAL_ROLES) != (z == ""):
            errs.append(ConfigError("role_zone_mismatch", f"{role} / {z!r}"))
        elif z and z not in zones:
            errs.append(ConfigError("unknown_zone", f"sensor {s.get('entity')}"))
        dom = str(s.get("entity", "")).split(".", 1)[0]
        if dom not in ROLE_DOMAINS[role]:
            errs.append(ConfigError("bad_sensor_domain", f"{s.get('entity')} as {role}"))
    for z in cfg.get("zones", []):
        if not any(s["role"] == "illuminance" and s.get("zone") == z["key"] for s in cfg.get("sensors", [])):
            errs.append(ConfigError("zone_needs_illuminance", z["key"]))
        for slot, key in (z.get("bindings") or {}).items():
            kind = BINDING_SLOTS.get(slot)
            if kind is None:
                errs.append(ConfigError("bad_binding", slot))
            elif key and kind == "group" and not any(g["key"] == key and g["zone"] == z["key"] for g in cfg["groups"]):
                errs.append(ConfigError("bad_binding", f"{slot} -> {key}"))
            elif key and kind == "lamp" and lamp_zone.get(key) != z["key"]:
                errs.append(ConfigError("bad_binding", f"{slot} -> {key}"))
    if not cfg.get("zones"):
        errs.append(ConfigError("no_zones"))
    return errs


def normalise(cfg: dict) -> dict:
    """Clean a config coming from the UI or storage: defaults for missing parts, parameters cleaned per level."""
    c = copy.deepcopy(cfg) if cfg else empty_config()
    c["version"] = CONFIG_VERSION
    c["params"] = P.clean_overrides(c.get("params", {}), P.G)
    c.setdefault("sensors", [])
    c.setdefault("zones", []); c.setdefault("lamps", []); c.setdefault("groups", [])
    for z in c["zones"]:
        z["params"] = P.clean_overrides(z.get("params", {}), P.Z)
        z["name"] = z.get("name") or z["key"]
        z["bindings"] = {k: v for k, v in (z.get("bindings") or {}).items() if v}
    for x in c["lamps"]:
        x["params"] = P.clean_overrides(x.get("params", {}), P.L)
        x["name"] = x.get("name") or x["key"]
    for g in c["groups"]:
        g["params"] = P.clean_overrides(g.get("params", {}), P.GR)
        g["name"] = g.get("name") or g["key"]
        g["lamps"] = list(dict.fromkeys(g.get("lamps", [])))
        g["share_pause"] = bool(g.get("share_pause", False))
    return c


@dataclass(frozen=True)
class Resolved:
    """A lamp with all its parameters resolved through the inheritance chain."""
    key: str
    entity: str
    zone: str
    name: str
    p: dict


class Config:
    def __init__(self, cfg: dict):
        self.raw = normalise(cfg)
        self.zones = {z["key"]: z for z in self.raw["zones"]}
        self.zone_order = [z["key"] for z in self.raw["zones"]]
        self.lamps = {x["key"]: x for x in self.raw["lamps"]}
        self.groups = {g["key"]: g for g in self.raw["groups"]}

    @property
    def primary_zone(self) -> str:
        return self.zone_order[0]

    def lamps_in_zone(self, zone: str) -> list[str]:
        return [k for k, x in self.lamps.items() if x["zone"] == zone]

    def groups_of(self, lamp_key: str) -> list[dict]:
        return [g for g in self.raw["groups"] if lamp_key in g["lamps"]]

    def sensors(self, role: str, zone: str = "") -> list[str]:
        return [s["entity"] for s in self.raw["sensors"] if s["role"] == role and s.get("zone", "") == zone]

    def sensor(self, role: str, zone: str = "") -> str | None:
        v = self.sensors(role, zone)
        return v[0] if v else None

    def zone_param(self, zone: str, name: str) -> Any:
        return P.resolve(name, global_p=self.raw["params"], zone_p=self.zones[zone]["params"], group_ps=[], lamp_p=None)

    def lamp_param(self, lamp_key: str, name: str) -> Any:
        x = self.lamps[lamp_key]
        return P.resolve(name, global_p=self.raw["params"], zone_p=self.zones[x["zone"]]["params"],
                         group_ps=[g["params"] for g in self.groups_of(lamp_key)], lamp_p=x["params"])

    def global_param(self, name: str) -> Any:
        return P.resolve(name, global_p=self.raw["params"], zone_p=None, group_ps=[], lamp_p=None)

    def lamp_params(self, lamp_key: str) -> dict:
        return {n: self.lamp_param(lamp_key, n) for n in P.PARAMS}

    def zone_params(self, zone: str) -> dict:
        return {n: self.zone_param(zone, n) for n in P.PARAMS}

    def flat(self, zone: str, lamp_key: str | None = None) -> dict:
        """Flat mapping with the naming the core formulas use (light_<shared>, light_k_<lamp>, light_<lamp>_<name>).

        Shared names come from the zone (lamp_key None) or from ``lamp_key``'s resolution; per-lamp names are
        filled for every lamp of the zone (the ambient needs every k)."""
        base = self.lamp_params(lamp_key) if lamp_key else self.zone_params(zone)
        out: dict[str, Any] = {f"light_{n}": base[n] for n in P.SHARED_NAMES + P.ZONE_NAMES + P.GLOBAL_NAMES}
        for k in self.lamps_in_zone(zone):
            lp = self.lamp_params(k)
            out[f"light_k_{k}"] = lp["k"]
            for n in P.LAMP_PARAM_NAMES:
                if n != "k":
                    out[f"light_{k}_{n}"] = lp[n]
        return out

    def to_dict(self) -> dict:
        return copy.deepcopy(self.raw)

    def entity_of(self, lamp_key: str) -> str:
        return self.lamps[lamp_key]["entity"]

    def binding(self, zone: str, slot: str) -> str | None:
        return self.zones[zone].get("bindings", {}).get(slot) or None

    def group_lamps(self, group_key: str) -> list[str]:
        return list(self.groups[group_key]["lamps"])


def plan_living_room() -> dict:
    """The user's living room exactly as the YAML plan names it (migration preset / default of the wizard)."""
    lamp = lambda key, ent, p: {"key": key, "entity": ent, "zone": "living_room", "name": key.replace("_", " ").title(), "params": p}
    prof = lambda mn, mx, em, k, **kw: {"min": mn, "max": mx, "evening_mult": em, "k": k, **kw}
    cfg = {
        "version": CONFIG_VERSION,
        "params": {},
        "sensors": [
            {"entity": "sensor.general_graz_meteo_station_global_radiation", "role": "radiation", "zone": ""},
            {"entity": "sensor.general_sun_solar_elevation", "role": "elevation", "zone": ""},
            *[{"entity": e, "role": r, "zone": "living_room"} for r, e in (
                ("illuminance", "sensor.living_room_presence_sensor_light_sensor_light_level"),
                ("presence", "binary_sensor.living_room_presence_sensor"),
                ("media", "media_player.living_room_tv"),
                ("sofa", "binary_sensor.living_room_presence_sensor_sofa_zone"),
                ("table", "binary_sensor.living_room_presence_right_table_zone"),
                ("fridge", "binary_sensor.living_room_presence_sensor_refrigerator_zone"),
                ("entrance", "binary_sensor.living_room_presence_sensor_entrance_zone"),
                ("carpet", "binary_sensor.living_room_presence_sensor_carpet_zone"),
                ("window_advice", "sensor.general_graz_meteo_station_window_open_advice"),
                ("window_left", "sensor.living_room_left_window_open_percentage"),
                ("window_right", "sensor.living_room_right_window_open_percentage"),
                ("vent_mode", "input_boolean.living_room_fan_vent_mode"),
                ("color_store", "input_text.living_room_top_lamps_color"))],
        ],
        "zones": [{"key": "living_room", "name": "Living room",
                   "params": {"mode_entity": "input_select.living_room_light_mode",
                              "scene_entity": "input_select.work_station_desk_light_scene"},
                   "bindings": {"top_group": "top", "desk_group": "desk_group", "wake_group": "wake",
                                "fridge_lamp": "bottom", "vent_left": "top_right", "vent_right": "bottom",
                                "green_lamp": "top_left"}}],
        "lamps": [
            lamp("top_left", "light.living_room_top_lamp_top_left", prof(10, 60, 0.5, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("top_right", "light.living_room_top_lamp_top_right", prof(10, 60, 0.5, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("bottom", "light.living_room_top_lamp_bottom", prof(5, 40, 0.6, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("desk", "light.work_station_lamp", prof(5, 100, 0.8, 0.5, ctrl_modes="Day,Evening,Movie", scene_scaled=1,
                                                         calib_levels="15,40,80")),
            lamp("stick", "light.work_station_light_stick", prof(5, 25, 0.8, 0.3, ctrl_modes="Day,Evening,Movie", scene_scaled=1,
                                                                 work_hours_only=1, legacy_ratio=0.25, calib_levels="5,15,30")),
        ],
        "groups": [
            {"key": "top", "name": "Top lamps", "zone": "living_room", "lamps": ["top_left", "top_right", "bottom"], "params": {}},
            {"key": "desk_group", "name": "Desk", "zone": "living_room", "lamps": ["desk", "stick"], "params": {}},
            {"key": "wake", "name": "Night wake", "zone": "living_room", "lamps": ["bottom", "top_left"], "params": {}},
        ],
    }
    return normalise(cfg)

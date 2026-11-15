"""Configuration model: zones, lamp groups, and one rule type.

Pure data + validation, no Home Assistant imports. The config flow edits exactly this structure;
the runtime reads it through ``Config``.

    {
      "version": 3,
      "params":  {name: value},                                  # global overrides
      "sensors": [{"entity", "role", "zone"}],                   # zone "" = global (radiation, elevation)
      "zones":   [{"key", "name", "params", "area_id"?}],        # one per room, the first is the primary one
      "lamps":   [{"key", "entity", "zone", "name", "params"}],
      "groups":  [{"key", "name", "zone", "lamps": [keys], "params", "share_pause"}],
      "rules":   [rule]                                          # one shape; the examples differ only by numbers
    }

A rule is zone, group, lux threshold, an action (step or target) and the coefficients
min, max, gain, evening_mult. Presence is a trigger next to lux. A time window is a condition.
min and max bound the action; they do not start it.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from . import params as P

CONFIG_VERSION = 3
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

GLOBAL_ROLES = ("radiation", "elevation")
ZONE_ROLES = ("illuminance", "presence", "media")
ROLES = GLOBAL_ROLES + ZONE_ROLES
ROLE_DOMAINS = {
    "illuminance": ("sensor",), "presence": ("binary_sensor",), "radiation": ("sensor",), "elevation": ("sensor",),
    "media": ("media_player", "binary_sensor", "switch", "remote"),
}
# Roles and binding slots that 0.4 no longer stores. Loaded configs drop them.
_DROPPED_ROLES = ("sofa", "table", "fridge", "entrance", "carpet", "window_advice", "window_left", "window_right",
                  "vent_mode", "color_store")

# One rule. Every example has these fields; only the numbers change.
RULE_FIELDS = ("zone", "group", "lux_below", "action", "pct", "min", "max", "gain", "evening_mult", "presence", "window")
RULE_ACTIONS = ("step", "target")
RULE_WINDOWS = ("", "evening")
_RULE_DEFAULTS = {
    "zone": "", "group": "", "lux_below": 0, "action": "target", "pct": 0,
    "min": 0, "max": 100, "gain": 1, "evening_mult": 1, "presence": False, "window": "",
}


def make_rule(**over) -> dict:
    """One rule. Unknown names are refused: the field list is frozen."""
    extra = set(over) - set(RULE_FIELDS)
    if extra:
        raise ConfigError("bad_rule", "fields " + ", ".join(sorted(extra)))
    rule = dict(_RULE_DEFAULTS)
    rule.update(over)
    return rule


def example_rules() -> list[dict]:
    """The three cases of the frozen 0.4 scheme. Data only: the same rule, different numbers."""
    return [
        make_rule(zone="work_station", group="task", lux_below=40, action="target", pct=70, max=80),
        make_rule(zone="living_room", group="fridge_glow", lux_below=15, action="step", pct=20, max=40,
                  presence=True, window="evening"),
        make_rule(zone="living_room", group="accent", lux_below=30, action="target", pct=25, evening_mult=0.5),
    ]


def empty_config() -> dict:
    return {"version": CONFIG_VERSION, "params": {}, "sensors": [], "zones": [], "lamps": [], "groups": [], "rules": []}


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
    """All problems of a config; empty list = usable. Codes are translation keys of the config flow.

    A zone does not need an illuminance sensor: without one the runtime estimates the room from daylight only."""
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
    groups_of = {}
    for g in cfg.get("groups", []):
        groups_of.setdefault(g.get("zone"), set()).add(g.get("key"))
    for rule in cfg.get("rules") or []:
        if not isinstance(rule, dict) or set(rule) - set(RULE_FIELDS):
            errs.append(ConfigError("bad_rule", "fields"))
            continue
        zone, group = rule.get("zone"), rule.get("group")
        if zone not in zones or group not in groups_of.get(zone, ()):
            errs.append(ConfigError("bad_rule", f"{zone}/{group}"))
        if rule.get("action") not in RULE_ACTIONS or rule.get("window") not in RULE_WINDOWS:
            errs.append(ConfigError("bad_rule", "action"))
        if not isinstance(rule.get("presence"), bool):
            errs.append(ConfigError("bad_rule", "presence"))
        for name in ("lux_below", "pct", "min", "max", "gain", "evening_mult"):
            v = rule.get(name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errs.append(ConfigError("bad_rule", name))
        else:
            if isinstance(rule.get("min"), (int, float)) and isinstance(rule.get("max"), (int, float)):
                if rule["min"] > rule["max"]:
                    errs.append(ConfigError("bad_rule", "min"))
    if not cfg.get("zones"):
        errs.append(ConfigError("no_zones"))
    return errs


def _num(v: Any) -> int | float:
    f = float(v)
    return int(f) if f == int(f) else f


def _clean_rule(rule: dict) -> dict:
    """Fill the frozen defaults. Extra names stay so validation can refuse them."""
    item = {k: rule[k] if k in rule else v for k, v in _RULE_DEFAULTS.items()}
    for k, v in rule.items():
        if k not in item:
            item[k] = v
    for name in ("lux_below", "pct", "min", "max", "gain", "evening_mult"):
        if name in rule and not isinstance(rule[name], bool):
            try:
                item[name] = _num(rule[name])
            except (TypeError, ValueError):
                item[name] = rule[name]
    if "presence" in rule:
        item["presence"] = rule["presence"] if isinstance(rule["presence"], bool) else rule["presence"]
    if item.get("window") is None:
        item["window"] = ""
    return item


def normalise(cfg: dict) -> dict:
    """Clean a config coming from the UI or storage: defaults for missing parts, parameters cleaned per level."""
    c = copy.deepcopy(cfg) if cfg else empty_config()
    c["version"] = CONFIG_VERSION
    c["params"] = P.clean_overrides(c.get("params", {}), P.G)
    c.setdefault("sensors", [])
    c["sensors"] = [s for s in c["sensors"] if s.get("role") not in _DROPPED_ROLES]
    c.setdefault("zones", []); c.setdefault("lamps", []); c.setdefault("groups", [])
    for z in c["zones"]:
        z["params"] = P.clean_overrides(z.get("params", {}), P.Z)
        z["name"] = z.get("name") or z["key"]
        z.pop("bindings", None)
        z.pop("detect_rules", None)
    for x in c["lamps"]:
        x["params"] = P.clean_overrides(x.get("params", {}), P.L)
        x["name"] = x.get("name") or x["key"]
    for g in c["groups"]:
        g["params"] = P.clean_overrides(g.get("params", {}), P.GR)
        g["name"] = g.get("name") or g["key"]
        g["lamps"] = list(dict.fromkeys(g.get("lamps", [])))
        g["share_pause"] = bool(g.get("share_pause", False))
    c["rules"] = [_clean_rule(r) for r in (c.get("rules") or []) if isinstance(r, dict)]
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

    def rules_for(self, zone: str, group: str) -> list[dict]:
        return [r for r in self.raw.get("rules") or [] if r.get("zone") == zone and r.get("group") == group]

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
                ("media", "media_player.living_room_tv"))],
        ],
        "zones": [{"key": "living_room", "name": "Living room",
                   "params": {"mode_entity": "input_select.living_room_light_mode",
                              "scene_entity": "input_select.work_station_desk_light_scene"}}],
        "lamps": [
            lamp("top_left", "light.living_room_top_lamp_top_left", prof(10, 60, 0.5, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("top_right", "light.living_room_top_lamp_top_right", prof(10, 60, 0.5, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("bottom", "light.living_room_top_lamp_bottom", prof(5, 40, 0.6, 0.1, ctrl_modes="Day,Evening", fridge_block=1)),
            lamp("desk", "light.work_station_lamp", prof(5, 100, 0.8, 0.5, ctrl_modes="Day,Evening,Movie", scene_scaled=1)),
            lamp("stick", "light.work_station_light_stick", prof(5, 25, 0.8, 0.3, ctrl_modes="Day,Evening,Movie", scene_scaled=1,
                                                                 work_hours_only=1, legacy_ratio=0.25)),
        ],
        "groups": [
            {"key": "top", "name": "Top lamps", "zone": "living_room", "lamps": ["top_left", "top_right", "bottom"], "params": {}},
            {"key": "desk_group", "name": "Desk", "zone": "living_room", "lamps": ["desk", "stick"], "params": {}},
            {"key": "wake", "name": "Night wake", "zone": "living_room", "lamps": ["bottom", "top_left"], "params": {}},
        ],
        "rules": [],
    }
    return normalise(cfg)

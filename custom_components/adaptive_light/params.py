"""Parameter registry and the global -> zone -> group -> lamp inheritance. Pure, no Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

G, Z, GR, L = "global", "zone", "group", "lamp"


@dataclass(frozen=True)
class Spec:
    default: Any
    kind: str = "number"          # number | text | bool
    lo: float = 0
    hi: float = 100
    step: float = 1
    unit: str | None = None
    levels: tuple[str, ...] = (G, Z, GR, L)   # levels where an override is allowed (and read)


_SHARED = (G, Z, GR, L)
_ZONE_ONLY = (G, Z)
_GLOBAL_ONLY = (G,)
_LAMPISH = (G, Z, GR, L)
_LAMP_ONLY = (L,)

PARAMS: dict[str, Spec] = {
    # shared by zone, group and lamp ("group settings")
    "target_lux_day": Spec(80, lo=0, hi=300, step=5, unit="lx", levels=_SHARED),
    "target_lux_evening": Spec(40, lo=0, hi=300, step=5, unit="lx", levels=_SHARED),
    "w_outdoor": Spec(0.4, lo=0, hi=1, step=0.05, levels=_SHARED),
    "w_indoor": Spec(0.6, lo=0, hi=1, step=0.05, levels=_SHARED),
    "evening_start_hour": Spec(21, lo=17, hi=23, levels=_SHARED),
    "scene_work": Spec(0.8, lo=0, hi=1.5, step=0.05, levels=_SHARED),
    "scene_bright": Spec(1.0, lo=0, hi=1.5, step=0.05, levels=_SHARED),
    "scene_soft": Spec(0.4, lo=0, hi=1.5, step=0.05, levels=_SHARED),
    "need_blind": Spec(40, lo=0, hi=100, unit="%", levels=_SHARED),
    "override_minutes": Spec(30, lo=1, hi=480, unit="min", levels=_SHARED),
    "mode_entity": Spec("", kind="text", levels=_SHARED),
    "scene_entity": Spec("", kind="text", levels=_SHARED),
    # per lamp profile (also settable on a group to share it)
    "min": Spec(5, lo=1, hi=100, unit="%", levels=_LAMPISH),
    "max": Spec(100, lo=1, hi=100, unit="%", levels=_LAMPISH),
    "gain": Spec(1.0, lo=0, hi=3, step=0.05, levels=_LAMPISH),
    "evening_mult": Spec(1.0, lo=0, hi=1.5, step=0.05, levels=_LAMPISH),
    "w_out": Spec(-1, lo=-1, hi=1, step=0.05, levels=_LAMPISH),
    "w_in": Spec(-1, lo=-1, hi=1, step=0.05, levels=_LAMPISH),
    "scene_scaled": Spec(0, kind="bool", levels=_LAMPISH),
    "work_hours_only": Spec(0, kind="bool", levels=_LAMPISH),
    "legacy_ratio": Spec(1.0, lo=0.05, hi=1, step=0.05, levels=_LAMPISH),
    "ctrl_modes": Spec("Day,Evening", kind="text", levels=_LAMPISH),
    "fridge_block": Spec(0, kind="bool", levels=_LAMPISH),
    "calib_levels": Spec("20,50,80", kind="text", levels=_LAMPISH),
    "k": Spec(0.1, lo=0.001, hi=100, step=0.001, unit="lx", levels=_LAMP_ONLY),
    # per zone sensors and blinds (a lamp cannot differ from its zone here)
    "dark_lux_offset": Spec(0, lo=0, hi=20, step=0.1, unit="lx", levels=_ZONE_ONLY),
    "blind_lux_floor": Spec(15, lo=0, hi=200, unit="lx", levels=_ZONE_ONLY),
    "open_k": Spec(1.0, lo=0.05, hi=20, step=0.01, unit="lx", levels=_ZONE_ONLY),
    "open_min_daylight": Spec(20, lo=0, hi=100, unit="%", levels=_ZONE_ONLY),
    "open_hyst": Spec(10, lo=0, hi=50, levels=_ZONE_ONLY),
    "open_confirm": Spec(5, lo=1, hi=30, levels=_ZONE_ONLY),
    # global only
    "radiation_full_scale": Spec(150, lo=10, hi=2000, unit="W/m2", levels=_GLOBAL_ONLY),
    "step_pct": Spec(10, lo=1, hi=50, unit="%", levels=_GLOBAL_ONLY),
    "threshold_pct": Spec(3, lo=1, hi=20, unit="%", levels=_GLOBAL_ONLY),
    "transition_s": Spec(5, lo=0, hi=60, unit="s", levels=_GLOBAL_ONLY),
}

LAMP_PARAM_NAMES = ("min", "max", "gain", "evening_mult", "w_out", "w_in", "scene_scaled", "work_hours_only",
                    "legacy_ratio", "ctrl_modes", "fridge_block", "calib_levels", "k")
SHARED_NAMES = tuple(n for n, s in PARAMS.items() if s.levels == _SHARED)
ZONE_NAMES = tuple(n for n, s in PARAMS.items() if s.levels == _ZONE_ONLY)
GLOBAL_NAMES = tuple(n for n, s in PARAMS.items() if s.levels == _GLOBAL_ONLY)

# which names the UI offers at each level
UI_NAMES = {
    G: SHARED_NAMES + ZONE_NAMES + GLOBAL_NAMES + tuple(n for n in LAMP_PARAM_NAMES if n != "k"),
    Z: SHARED_NAMES + ZONE_NAMES,
    GR: SHARED_NAMES + tuple(n for n in LAMP_PARAM_NAMES if n != "k"),
    L: SHARED_NAMES + LAMP_PARAM_NAMES,
}


def coerce(name: str, value: Any) -> Any:
    spec = PARAMS[name]
    if spec.kind == "text":
        return "" if value is None else str(value)
    if spec.kind == "bool":
        return 1 if value in (True, 1, "1", "on", "true", "True") else 0
    v = float(value)
    return min(max(v, spec.lo), spec.hi)


def clean_overrides(overrides: dict[str, Any], level: str) -> dict[str, Any]:
    """Drop unknown names and names not allowed at this level; coerce/clamp the rest."""
    out = {}
    for n, v in (overrides or {}).items():
        s = PARAMS.get(n)
        if s is None or level not in s.levels or v is None or v == "":
            continue
        out[n] = coerce(n, v)
    return out


def resolve(name: str, *, global_p: dict, zone_p: dict | None, group_ps: list[dict], lamp_p: dict | None) -> Any:
    """Lamp overrides win, then groups (later group in config order wins), then zone, then global, then default."""
    s = PARAMS[name]
    chain: list[tuple[str, dict | None]] = [(L, lamp_p)] + [(GR, g) for g in reversed(group_ps)] + [(Z, zone_p), (G, global_p)]
    for level, d in chain:
        if d is not None and level in s.levels and name in d:
            return d[name]
    return s.default


def parse_modes(text: str) -> tuple[str, ...]:
    return tuple(m.strip() for m in str(text).split(",") if m.strip())


def parse_levels(text: str) -> tuple[int, int, int]:
    try:
        v = tuple(int(float(x)) for x in str(text).split(","))
    except ValueError:
        v = ()
    if len(v) != 3 or not all(1 <= x <= 100 for x in v):
        return (20, 50, 80)
    return v

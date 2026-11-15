"""Pure adaptive-light logic: no Home Assistant imports, fully unit-testable.

Every function mirrors a macro / template / automation block of the YAML plan
(docs/adaptive-light-plan.md). Inputs that are unavailable in HA are passed as None
and fall back to the same defaults the Jinja ``| float(default)`` filters use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

OWN_CHANGE_WINDOW_S = 60
OTHER_AUTOMATION_WINDOW_S = 15


def clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _f(v: float | None, default: float) -> float:
    return default if v is None else float(v)


@dataclass
class LampState:
    on: bool
    brightness: int | None = None  # 0..255 as HA reports it


def daylight_factor(radiation: float | None, elevation: float | None, full_scale: float = 150) -> int | None:
    """sensor.daylight_factor, 0..100. None = unavailable (both inputs missing)."""
    if radiation is not None:
        v = radiation / full_scale
    elif elevation is not None:
        v = (elevation + 4) / 20
    else:
        return None
    return int(round(clamp(v, 0, 1) * 100))


def lamps_on(lamps: Mapping[str, LampState]) -> bool:
    return any(s.on for s in lamps.values())


def lamps_lx(lamps: Mapping[str, LampState], p: Mapping[str, float]) -> float:
    total = 0.0
    for key, s in lamps.items():
        if s.on:
            total += _f(p.get(f"light_k_{key}"), 0) * (s.brightness or 0) / 255 * 100
    return total


def ambient(lux: float | None, lamps: Mapping[str, LampState], p: Mapping[str, float]) -> float:
    raw = _f(lux, 0) - _f(p.get("light_dark_lux_offset"), 0) - lamps_lx(lamps, p)
    return round(max(raw, 0), 1)


def estimate_lux(daylight: float | None, lamps: Mapping[str, LampState], p: Mapping[str, float]) -> float | None:
    """Illuminance a zone WITHOUT a sensor would read: daylight through the windows (open_k lx per 1% of daylight),
    the dark offset and the zone's own lamps. None when the daylight factor is unavailable.

    Feeding this into ``ambient`` gives back exactly ``open_k * daylight``: with no sensor the room is assumed
    to be lit by daylight only, and the lamps cannot influence the estimate (no feedback loop)."""
    if daylight is None:
        return None
    return round(_f(p.get("light_open_k"), 1) * daylight + _f(p.get("light_dark_lux_offset"), 0) + lamps_lx(lamps, p), 1)


def trust(
    daylight: float | None,
    openness: float | None,
    lux: float | None,
    lamps: Mapping[str, LampState],
    p: Mapping[str, float],
) -> int:
    dl = _f(daylight, 0)
    min_dl = _f(p.get("light_open_min_daylight"), 20)
    o = (_f(openness, 100) / 100) if dl >= min_dl else 1
    floor = _f(p.get("light_blind_lux_floor"), 15)
    expected = _f(p.get("light_open_k"), 1) * dl
    amb = ambient(lux, lamps, p)
    if (not lamps_on(lamps)) or floor <= 0 or expected <= 2 * floor:
        soft = 1
    else:
        soft = min(max((amb - 0.5 * floor) / (0.5 * floor), 0), 1)
    return int(round(100 * min(o, soft)))


@dataclass
class Inputs:
    """Snapshot of everything the formulas read."""

    lux: float | None
    daylight: float | None
    openness: float | None
    mode: str
    scene: str | None
    evening_quiet: bool
    lamps: Mapping[str, LampState]
    params: Mapping[str, float] = field(default_factory=dict)


def need(key: str, i: Inputs) -> float:
    """``key`` '' = global weights (sensor.light_need), else the lamp's own weights if set."""
    p = i.params
    wo = _f(p.get(f"light_{key}_w_out"), -1) if key else -1
    wi = _f(p.get(f"light_{key}_w_in"), -1) if key else -1
    if wo < 0 or wi < 0 or wo + wi <= 0:
        wo = _f(p.get("light_w_outdoor"), 0.4)
        wi = _f(p.get("light_w_indoor"), 0.6)
    d = clamp(_f(i.daylight, 0) / 100, 0, 1)
    amb = ambient(i.lux, i.lamps, p)
    tgt = _f(p.get("light_target_lux_day" if i.mode == "Day" else "light_target_lux_evening"), 60)
    room = clamp(amb / tgt, 0, 1) if tgt > 0 else 1
    n = 100 * (wo * (1 - d) + wi * (1 - room)) / (wo + wi) if wo + wi > 0 else 50
    nb = _f(p.get("light_need_blind"), 40)
    tr = trust(i.daylight, i.openness, i.lux, i.lamps, p) / 100
    return nb + tr * (n - nb)




def target(key: str, i: Inputs) -> int:
    p = i.params
    mn = _f(p.get(f"light_{key}_min"), 5)
    mx = _f(p.get(f"light_{key}_max"), 100)
    g = _f(p.get(f"light_{key}_gain"), 1)
    x = clamp(need(key, i) / 100 * g, 0, 1)
    t = mn + (mx - mn) * x
    if i.evening_quiet:
        t *= _f(p.get(f"light_{key}_evening_mult"), 1)
    if p.get(f"light_{key}_scene_scaled"):
        mult = {
            "Work": p.get("light_scene_work"),
            "Bright": p.get("light_scene_bright"),
            "Soft": p.get("light_scene_soft"),
        }.get(i.scene or "", 1)
        t *= _f(mult, 1)
    return int(round(clamp(t, mn, mx)))


def evening_quiet(hour: int, p: Mapping[str, float]) -> bool:
    return hour >= int(_f(p.get("light_evening_start_hour"), 21))


# ---------------------------------------------------------------- openness (step 2a)

def openness_raw(
    lux: float | None,
    daylight: float | None,
    mode: str,
    lamps: Mapping[str, LampState],
    p: Mapping[str, float],
) -> int | None:
    """sensor.light_openness_raw; None = unavailable (estimate not clean)."""
    if lux is None or daylight is None:
        return None
    k = _f(p.get("light_open_k"), 0)
    if not k > 0:
        return None
    if daylight < _f(p.get("light_open_min_daylight"), 20):
        return None
    if mode not in ("Day", "Evening") or lamps_on(lamps):
        return None
    expected = k * daylight
    if expected > 0:
        return int(round(100 * clamp(lux / expected, 0, 1)))
    return 100


@dataclass
class OpennessState:
    value: float = 100.0
    low_count: int = 0


def openness_step(
    s: OpennessState, raw: float | None, p: Mapping[str, float], *, reset: bool = False
) -> OpennessState:
    """One tick of the smoothing template sensor (trigger id 'tick' or 'reset')."""
    if reset:
        return OpennessState(100.0, 0)
    hyst = _f(p.get("light_open_hyst"), 10)
    confirm = int(_f(p.get("light_open_confirm"), 5))
    valid = raw is not None
    prev = s.value
    lc = s.low_count
    lc_new = (lc + 1 if raw < prev - hyst else 0) if valid else lc
    if not valid:
        new = prev
    elif raw > prev + hyst:
        new = prev + 0.3 * (raw - prev)
    elif lc_new >= confirm:
        new = prev + 0.5 * (raw - prev)
    else:
        new = prev
    return OpennessState(round(new, 1), lc_new)


# ---------------------------------------------------------------- controller (step 6.2)

@dataclass
class LampDecision:
    key: str
    now_pct: int
    target: int
    new_pct: int
    act: bool
    entities: list[str]
    reason: str  # why not acting (for logs); '' when act


def decide_lamp(
    key: str,
    *,
    entity: str,
    lamp: LampState,
    target_pct: int,
    mode: str,
    fridge_busy: bool,
    switch_on: bool,
    now_ts: float,
    pause_until_ts: float,
    modes: Iterable[str],
    fridge_block: bool,
    step_pct: int = 10,
    threshold_pct: int = 3,
) -> LampDecision:
    allowed = switch_on and mode in modes and not (fridge_block and fridge_busy)
    on_ents = [entity] if lamp.on else []
    paused = now_ts < pause_until_ts
    now_pct = int(round((lamp.brightness or 0) / 255 * 100)) if on_ents else 0
    new_pct = now_pct + int(clamp(target_pct - now_pct, -step_pct, step_pct))
    act = (
        allowed
        and bool(on_ents)
        and target_pct >= 0
        and not paused
        and abs(target_pct - now_pct) >= threshold_pct
    )
    if act:
        reason = ""
    elif not switch_on:
        reason = "switch off"
    elif not allowed:
        reason = "mode/fridge"
    elif paused:
        reason = "paused"
    elif target_pct < 0:
        reason = "target unavailable"
    else:
        reason = f"within {threshold_pct}%"
    return LampDecision(key, now_pct, target_pct, new_pct, act, on_ents, reason)


# ---------------------------------------------------------------- override (step 6.1)

def override_pause_until(
    *,
    now_ts: float,
    last_set_ts: float,
    parent_id_is_none: bool,
    to_state_on: bool,
    had_from_state: bool,
    automations_last_triggered: Iterable[tuple[str, float]],
    ignore_names: Iterable[str],
    switch_on: bool,
    minutes: float | None,
) -> float | None:
    """Return the new pause_until timestamp, or None when the change is not manual."""
    if not (had_from_state and to_state_on and parent_id_is_none):
        return None
    if not (now_ts - last_set_ts) > OWN_CHANGE_WINDOW_S:
        return None
    skip = set(ignore_names)
    for name, last in automations_last_triggered:
        if name not in skip and now_ts - last < OTHER_AUTOMATION_WINDOW_S:
            return None
    if not switch_on:
        return None
    m = _f(minutes, 0)
    m = m if m >= 1 else 30
    return now_ts + m * 60


# ---------------------------------------------------------------- one rule (0.4)

def rule_fires(rule: Mapping, *, lux: float | None, presence: bool, evening: bool) -> bool:
    """Lux below the threshold opens the rule. Presence is a second trigger. A time window is a condition.

    min and max are not read here: they bound the action, they do not start it.
    """
    if lux is None or not (float(lux) < float(rule["lux_below"])):
        return False
    if rule.get("presence") and not presence:
        return False
    if rule.get("window") == "evening" and not evening:
        return False
    return True


def rule_brightness(rule: Mapping, *, now_pct: float, evening: bool) -> int:
    """The action, scaled by gain and (in the evening) evening_mult, then bounded by min and max.

    ``step`` adds ``pct`` to the brightness the lamp already has. ``target`` aims at ``pct``.
    """
    gain = float(rule.get("gain", 1))
    mult = float(rule.get("evening_mult", 1)) if evening else 1.0
    if rule.get("action") == "step":
        raw = float(now_pct) + float(rule["pct"]) * gain * mult
    else:
        raw = float(rule["pct"]) * gain * mult
    return int(round(clamp(raw, float(rule.get("min", 0)), float(rule.get("max", 100)))))


"""Ports of the plan's executor automations 5.2-5.12 as pure functions (decisions only, no HA, no I/O).

Each function returns a list of steps; the runtime layer (not written yet, depends on the agreed config model)
turns lamp *keys* into entity ids, runs the steps, and owns the triggers (``for:`` timers, state changes).
What lives here is exactly what the YAML expresses in ``conditions`` and ``actions``.

Gating rule (plan, section "Выключатели ламп"): everything that touches a lamp goes through ``managed()``;
a lamp whose auto switch is off is never touched, snapshots and restores included.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence, Union



# ------------------------------------------------------------------ steps

@dataclass(frozen=True)
class Cmd:
    action: str            # 'on' | 'off'
    keys: tuple[str, ...]
    data: tuple = ()       # sorted (name, value) pairs, e.g. (('brightness_pct', 8), ('transition', 3))

    @property
    def d(self) -> dict:
        return dict(self.data)


@dataclass(frozen=True)
class Delay:
    seconds: float


@dataclass(frozen=True)
class Snapshot:
    name: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Restore:
    name: str
    transition: float = 0


@dataclass(frozen=True)
class MarkOwn:
    """Write light_adaptive_last_set (strictly before the lamp command: opens the override ignore window)."""


@dataclass(frozen=True)
class Log:
    message: str


Step = Union[Cmd, Delay, Snapshot, Restore, MarkOwn, Log]


def on(keys: Iterable[str], **data) -> Cmd:
    return Cmd("on", tuple(keys), tuple(sorted(data.items())))


def off(keys: Iterable[str], **data) -> Cmd:
    return Cmd("off", tuple(keys), tuple(sorted(data.items())))


# ------------------------------------------------------------------ common

@dataclass(frozen=True)
class Bind:
    """Which lamps play which part in one zone (resolved from the zone's bindings and groups)."""

    top: tuple[str, ...] = ()
    desk: tuple[str, ...] = ()
    wake: tuple[str, ...] = ()
    fridge: str | None = None
    vent_left: str | None = None
    vent_right: str | None = None
    green: str | None = None


@dataclass
class Ctx:
    """Everything an executor may read. Lamp state per key: (is_on, brightness 0..255 or None, color_temp_kelvin or None)."""

    auto: Mapping[str, bool]
    bind: Bind = field(default_factory=Bind)
    lamp_params: Mapping[str, Mapping] = field(default_factory=dict)   # key -> {"work_hours_only", "legacy_ratio"}
    mode: str = "Day"
    lux: float | None = None
    hour: int = 12
    now_ts: float = 0.0
    last_set_ts: float = 0.0
    enabled: bool = False
    apply: bool = False
    lamps: Mapping[str, tuple[bool, int | None, int | None]] = field(default_factory=dict)
    targets: Mapping[str, int | None] = field(default_factory=dict)
    scene: str | None = None
    need: float | None = None
    ambient: float | None = None
    daylight: float | None = None

    def is_on(self, key: str) -> bool:
        return bool(self.lamps.get(key, (False, None, None))[0])

    @property
    def adaptive(self) -> bool:
        return self.enabled and self.apply


def managed(keys: Sequence[str], auto: Mapping[str, bool]) -> list[str]:
    """Subset (order kept, no duplicates) of lamp keys whose auto switch is on."""
    out: list[str] = []
    for k in keys:
        if auto.get(k) and k not in out:
            out.append(k)
    return out


def _ceil(x: float) -> int:
    return int(math.ceil(x))


def fade_wait(now_ts: float, last_set_ts: float) -> int:
    """Wait for a running controller fade (6 s after our last own change), as in 5.2/5.7."""
    return _ceil(max(0.0, 6 - (now_ts - last_set_ts)))


def _pct(brightness: int | None) -> int:
    return int(round((brightness or 0) / 255 * 100))


# ------------------------------------------------------------------ 5.2 movie mode

MOVIE_DIM_PCT = 8
MOVIE_BRIGHT_LUX = 40


def movie_start(c: Ctx) -> list[Step]:
    top = managed(c.bind.top, c.auto)
    if not top:
        return []
    bright = (c.lux or 0) >= MOVIE_BRIGHT_LUX
    steps: list[Step] = [Delay(fade_wait(c.now_ts, c.last_set_ts)), Snapshot("movie_before", tuple(top))]
    if any(c.is_on(k) for k in top) or not bright:
        steps += [MarkOwn(), on(top, brightness_pct=MOVIE_DIM_PCT, transition=3)]
    return steps


def movie_end(c: Ctx, to_mode: str) -> list[Step]:
    if to_mode not in ("Day", "Evening"):
        return []
    top = managed(c.bind.top, c.auto)
    if not top:
        return []
    steps: list[Step] = [MarkOwn()]
    if (c.lux or 0) >= MOVIE_BRIGHT_LUX:
        steps.append(off(top, transition=3))
    else:
        # the plan's scene restore cannot filter lamps: the snapshot itself only holds lamps that were managed at Movie start
        steps.append(Restore("movie_before", 3))
    return steps


# ------------------------------------------------------------------ 5.3 vent feedback

def vent_allowed(
    *, vent_mode: bool, mode: str, presence: bool, fridge_busy: bool, c: Ctx,
    trigger: str, from_value: float = 0.0, to_value: float = 0.0,
) -> bool:
    if not vent_mode or mode in ("Night", "Away", "Movie") or not presence or fridge_busy:
        return False
    if not managed([x for x in (c.bind.vent_left, c.bind.vent_right) if x], c.auto):
        return False
    if trigger == "advice_changed":
        return True
    return abs(to_value - from_value) > 3


def vent_blink(c: Ctx, *, trigger: str, advice: str, mode_now: str | None = None, mode_at_start: str | None = None) -> list[Step]:
    """Blink pattern of 5.3. top_right = 'left' lamp, bottom = 'right' lamp (as in the YAML)."""
    left, right = c.bind.vent_left, c.bind.vent_right
    if not left or not right:
        return []

    def bri(k: str) -> int:
        return int(min(max(_pct(c.lamps.get(k, (False, None, None))[1]), 20), 60))

    lb, rb = bri(left), bri(right)
    moved, moved_b = (left, lb) if trigger == "left_moved" else (right, rb)
    other, other_b = (right, rb) if trigger == "left_moved" else (left, lb)
    wrong = (trigger == "left_moved" and advice == "open_right") or (trigger == "right_moved" and advice == "open_left")
    lm, rm = managed([left], c.auto), managed([right], c.auto)
    both = managed([left, right], c.auto)
    mm, om = managed([moved], c.auto), managed([other], c.auto)
    s: list[Step] = [MarkOwn(), Snapshot("window_lights_snapshot", tuple(both))]
    if trigger == "advice_changed":
        for _ in range(2):
            s += [on(lm, color_name="blue", brightness_pct=lb), on(rm, color_name="blue", brightness_pct=rb),
                  Delay(0.3), off(both), Delay(0.3)]
        if advice in ("open_left", "open_both", "open_any_one"):
            s.append(on(lm, color_name="green", brightness_pct=lb))
        if advice in ("open_right", "open_both", "open_any_one"):
            s.append(on(rm, color_name="green", brightness_pct=rb))
        s.append(Delay(2))
    elif wrong:
        for _ in range(3):
            s += [on(mm, color_name="red", brightness_pct=moved_b), on(om, color_name="green", brightness_pct=other_b),
                  Delay(0.3), off(both), Delay(0.3)]
    else:
        for _ in range(2):
            s += [on(mm, color_name="green", brightness_pct=moved_b), Delay(0.4), off(mm), Delay(0.4)]
    return [x for x in s if not (isinstance(x, Cmd) and not x.keys)]


def vent_restore_needed(mode_now: str, mode_at_start: str) -> bool:
    return mode_now == mode_at_start


# ------------------------------------------------------------------ 5.4 evening top lamps

def evening_allowed(c: Ctx, *, presence: bool, group_off: bool) -> bool:
    return c.mode == "Evening" and presence and group_off and (c.lux is not None and c.lux < 40)


def evening_on(c: Ctx) -> list[Step]:
    legacy = 30 if c.hour >= 21 else 60
    keys = c.bind.top
    pct = {k: (c.targets.get(k) if c.targets.get(k) is not None else legacy) for k in keys}
    use = {k: (pct[k] if c.adaptive else legacy) for k in keys}
    steps: list[Step] = [MarkOwn()]
    for k in keys:
        if c.auto.get(k):
            steps.append(on([k], brightness_pct=use[k], color_temp_kelvin=2700, transition=3))
    if not c.adaptive:
        would = " / ".join(f"{k} {pct[k]}%" for k in keys)
        steps.append(Log(f"evening top lamps: used {legacy}%, adaptive would be {would} "
                         f"(need {c.need}, ambient {c.ambient} lx, daylight {c.daylight})."))
    return steps


# ------------------------------------------------------------------ 5.5 / 5.6 desk

SCENE_PCT = {"Work": 80, "Bright": 100, "Soft": 40}


def desk_legacy(scene: str | None, lux: float | None, ratio: float = 1.0) -> int:
    """Old (shadow) formula of 5.5/5.6. ratio 1 = the desk lamp, 0.25 = the stick (lamp / 4)."""
    scene_pct = SCENE_PCT.get(scene or "", 80)
    factor = min(0.45 + 0.55 * (lux or 0) / 100, 1)
    lamp = int(max(round(scene_pct * factor), 5))
    return lamp if ratio == 1 else int(max(round(lamp * ratio), 5))


def _desk_rows(c: Ctx, keys: Iterable[str]) -> list[tuple[str, int, int, int]]:
    """(key, legacy, adaptive, used) per lamp."""
    rows = []
    for k in keys:
        ratio = float(c.lamp_params.get(k, {}).get("legacy_ratio", 1.0))
        leg = desk_legacy(c.scene, c.lux, ratio)
        ada = c.targets.get(k) if c.targets.get(k) is not None else leg
        rows.append((k, leg, ada, ada if c.adaptive else leg))
    return rows


def _shadow_log(prefix: str, rows) -> Log:
    used = " / ".join(f"{k} {leg}%" for k, leg, _, _ in rows)
    would = " / ".join(f"{k} {ada}%" for k, _, ada, _ in rows)
    return Log(f"{prefix}: used {used}, adaptive would be {would}.")


def desk_on_allowed(c: Ctx, *, at_table: bool) -> bool:
    return c.mode not in ("Away", "Night") and at_table


def desk_on(c: Ctx) -> list[Step]:
    rows = _desk_rows(c, c.bind.desk)
    steps: list[Step] = [MarkOwn()]
    for k, _, _, used in rows:
        if not c.auto.get(k):
            continue
        if c.lamp_params.get(k, {}).get("work_hours_only") and not (6 <= c.hour < 15):
            continue
        steps.append(on([k], brightness_pct=used, transition=5))
    if not c.adaptive:
        steps.append(_shadow_log(f"desk at table (scene {c.scene})", rows))
    return steps


def scene_apply(c: Ctx) -> list[Step]:
    if c.mode in ("Away", "Night"):
        return []
    rows = _desk_rows(c, c.bind.desk)
    steps: list[Step] = [Delay(1), MarkOwn()]
    for k, _, _, used in rows:
        if c.is_on(k) and c.auto.get(k):
            steps.append(on([k], brightness_pct=used, transition=2))
    if not c.adaptive:
        steps.append(_shadow_log(f"desk scene {c.scene}", rows))
    return steps


# ------------------------------------------------------------------ 5.7 fridge

FRIDGE_PCT = 30
FRIDGE_KELVIN = 4500
FRIDGE_WAIT_MAX_S = 600
FRIDGE_LEAVE_S = 10


def fridge_allowed(c: Ctx) -> bool:
    return c.mode != "Away" and bool(c.bind.fridge) and bool(c.auto.get(c.bind.fridge))


def fridge_context(c: Ctx) -> dict:
    """Values captured at start: whether the room lamps were in use (bottom lamp already fridge-white does not count)."""
    b = c.lamps.get(c.bind.fridge or "", (False, None, None))
    white = b[2] == FRIDGE_KELVIN
    others = [k for k in c.bind.top if k != c.bind.fridge]
    was_on = any(c.is_on(k) for k in others) or (b[0] and not white)
    return {"white": white, "was_on": bool(was_on)}


def fridge_start(c: Ctx) -> list[Step]:
    if not fridge_allowed(c):
        return []
    return [Delay(fade_wait(c.now_ts, c.last_set_ts)), MarkOwn(), Snapshot("fridge_before", (c.bind.fridge,)),
            on([c.bind.fridge], brightness_pct=FRIDGE_PCT, color_temp_kelvin=FRIDGE_KELVIN)]


def fridge_end(c: Ctx, ctx0: dict) -> list[Step]:
    """After the zone stayed off for FRIDGE_LEAVE_S (or the 600 s cap). Gate is checked again here."""
    if not c.bind.fridge or not c.auto.get(c.bind.fridge):
        return [MarkOwn()]
    if ctx0["was_on"] and not ctx0["white"]:
        return [MarkOwn(), Restore("fridge_before", 3)]
    return [MarkOwn(), off([c.bind.fridge], transition=3)]


# ------------------------------------------------------------------ 5.8-5.10 off automations

def night_off_allowed(c: Ctx, *, trigger: str, tv_on: bool, presence: bool, table: bool, carpet: bool,
                      entrance: bool, fridge: bool) -> bool:
    if c.mode != "Night" or tv_on:
        return False
    if trigger == "night_start":
        return not presence
    if trigger == "sofa_asleep":
        return not (table or carpet or entrance or fridge)
    return False


def night_off(c: Ctx) -> list[Step]:
    m = managed(c.bind.top + c.bind.desk, c.auto)
    return [off(m)] if m else []


def desk_off_allowed(c: Ctx) -> bool:
    return c.mode != "Night"


def desk_off(c: Ctx) -> list[Step]:
    m = managed(c.bind.desk, c.auto)
    return [off(m, transition=3)] if m else []


def top_off_allowed(c: Ctx, *, tv_on: bool, presence: bool) -> bool:
    if c.mode == "Night":
        return False
    return c.mode == "Away" or (not tv_on and not presence)


def top_off(c: Ctx) -> list[Step]:
    m = managed(c.bind.top, c.auto)
    return [off(m)] if m else []


# ------------------------------------------------------------------ 5.11 green fill

def green_fill(c: Ctx, *, trigger: str, at_desk: bool) -> list[Step]:
    g = c.bind.green
    if c.mode not in ("Day", "Evening") or not g or not c.auto.get(g):
        return []
    lux = -1.0 if c.lux is None else c.lux
    work_hours = 6 <= c.hour < 15
    if trigger in ("leave", "end") or not work_hours:
        return [off([g])]
    if at_desk and 0 <= lux < 30 and trigger in ("dark", "enter"):
        return [on([g], brightness_pct=42, hs_color=(120, 73), transition=3)]
    if lux > 80 and trigger in ("bright", "enter"):
        return [off([g])]
    return []


# ------------------------------------------------------------------ 5.12 night wake dim

def parse_saved_color(saved: str) -> tuple[str, object]:
    """input_text value: 'hs:H,S' or 'k:KELVIN'; anything else -> 2700 K."""
    saved = (saved or "").strip()
    if saved.startswith("hs:"):
        parts = []
        for x in saved[3:].split(","):
            try:
                parts.append(float(x))
            except ValueError:
                parts.append(0.0)  # same as the plan: map('float', 0)
        if len(parts) == 2:
            return "hs", (parts[0], parts[1])
        return "k", 2700
    if saved.startswith("k:"):
        try:
            return "k", int(float(saved[2:]))
        except ValueError:
            return "k", 2700
    return "k", 2700


def night_wake(c: Ctx, *, trigger: str, saved_color: str) -> list[Step]:
    if c.mode != "Night":
        return []
    m = managed(c.bind.wake, c.auto)
    if trigger == "quiet":
        return [off(m, transition=3)] if m else []
    if not m or any(c.is_on(k) for k in m):
        return []
    kind, val = parse_saved_color(saved_color)
    if kind == "hs":
        return [on(m, brightness_pct=5, hs_color=val, transition=2)]
    return [on(m, brightness_pct=5, color_temp_kelvin=val, transition=2)]

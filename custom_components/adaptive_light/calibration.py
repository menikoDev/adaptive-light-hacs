"""Night calibration of lux-per-percent (plan step 7) and daily open_k (step 7b).

Calibration conditions (sun, mode, presence, TV, background light) are advice, not gates: they produce
warnings and the user decides. Only technical impossibilities refuse or abort (no illuminance sensor,
already running, a lamp that does not change the lux at all).

The fit and the checks are pure. ``run_calibration`` drives any object implementing
``CalibrationIO`` (the real one wraps hass services; tests use a simulated room).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_LEVELS = (20, 50, 80)
LEVELS = {
    "top_left": (20, 50, 80),
    "top_right": (20, 50, 80),
    "bottom": (20, 50, 80),
    "desk": (15, 40, 80),
    "stick": (5, 15, 30),
}
MAX_BG = 5.0
SUN_LIMIT = -8.0
MIN_GROWTH = 1.0


class CalibrationAborted(Exception):
    pass


CAUTION_EN = ("Do not turn the lamps on or off and do not change them by hand while the calibration runs: the lamps are driven "
              "automatically and any manual change spoils the measurement. Daylight, people in the room and a running TV or "
              "monitor distort the result.")
CAUTION_RU = ("Не включайте, не выключайте и не меняйте лампы вручную, пока идёт калибровка: лампами управляет интеграция, "
              "любое ручное изменение портит измерение. Дневной свет, присутствие людей и работающий ТВ или монитор искажают результат.")
CAUTION = f"{CAUTION_EN}\n\n{CAUTION_RU}"


def condition_warnings(*, has_elevation: bool = False, elevation: float | None = None, has_mode: bool = False, mode: str | None = None,
                       presence_on: bool = False, active_media: "list[str] | None" = None) -> list[str]:
    """Advisory warnings about the conditions of a night calibration (empty = ideal). Never a reason to refuse.

    ``has_elevation`` / ``has_mode``: the zone has such an entity configured (otherwise there is nothing to judge)."""
    out = []
    if has_elevation:
        if elevation is None:
            out.append("the solar elevation is unknown: cannot tell whether it is dark")
        elif not elevation < SUN_LIMIT:
            out.append(f"the sun is at {elevation:g} degrees (above {SUN_LIMIT:g}): daylight will distort the measurement")
    if has_mode and mode != "Night":
        out.append(f"zone mode is {mode or 'unknown'}, not Night")
    if presence_on:
        out.append("presence detected: people in the room change the light and may touch the lamps")
    for m in active_media or []:
        out.append(f"{m} is active: its screen adds light and flicker")
    return out


@dataclass
class LampFit:
    key: str
    base: float
    lux: tuple[float, float, float]
    k_fit: float
    k_lo: float
    k_hi: float
    warnings: list[str] = field(default_factory=list)


def fit_lamp(key, levels, lux, base_before, base_after) -> LampFit:
    """Least squares through zero over three levels (same maths as the YAML script)."""
    p1, p2, p3 = levels
    l1, l2, l3 = lux
    base = (base_before + base_after) / 2
    d1, d2, d3 = l1 - base, l2 - base, l3 - base
    k_fit = round((d1 * p1 + d2 * p2 + d3 * p3) / (p1 * p1 + p2 * p2 + p3 * p3), 3)
    k_lo = round(d1 / p1, 3)
    k_hi = round(d3 / p3, 3)
    nonlin = round(k_hi / k_lo, 2) if k_lo > 0 else 0
    warn = []
    if abs(base_after - base_before) > 2:
        warn.append(f"drift between background before and after {round(base_after - base_before, 1)} lx")
    if not l3 > l1:
        warn.append(f"not monotonic ({l1} / {l2} / {l3})")
    if k_lo > 0 and (nonlin < 0.6 or nonlin > 1.6):
        warn.append(f"nonlinear, k(high)/k(low) = {nonlin}")
    return LampFit(key, base, (l1, l2, l3), k_fit, k_lo, k_hi, warn)


class CalibrationIO(Protocol):
    async def snapshot(self) -> None: ...
    async def restore(self) -> None: ...
    async def all_off(self) -> None: ...
    async def set_lamp(self, key: str, pct: int | None) -> None: ...  # None = off
    async def read_lux(self) -> float | None: ...
    async def presence_on(self) -> bool: ...
    async def sleep(self, seconds: float) -> None: ...


@dataclass
class CalibrationResult:
    bg0: float
    fits: dict[str, LampFit]
    warnings: list[str] = field(default_factory=list)

    @property
    def candidates(self) -> dict[str, float]:
        return {k: f.k_fit for k, f in self.fits.items()}


async def run_calibration(
    io: CalibrationIO,
    settle_s: float = 60,
    lamps: "dict[str, tuple[int, int, int]] | None" = None,
    progress=None,
) -> CalibrationResult:
    """Never writes parameters; returns candidates. Always restores the lamps (also on cancel / error).

    ``lamps`` maps lamp key -> three test levels; default is the plan's five lamps. The set of keys is a
    parameter so the calibration does not depend on how lamps end up being configured.
    """
    lamps = lamps or LEVELS
    warnings: list[str] = []
    await io.snapshot()
    try:
        await io.all_off()
        await io.sleep(round(settle_s * 1.5))
        bg0 = await io.read_lux()
        if bg0 is None:
            raise CalibrationAborted("no illuminance reading with all lamps off")
        if bg0 > MAX_BG:
            warnings.append(f"background {bg0} lx with all lamps off is not darkness (expected up to {MAX_BG} lx): k may be inaccurate, "
                            f"and calibrate_apply would write dark_lux_offset = {round(min(bg0, DARK_RANGE[1]), 2)}")
        fits: dict[str, LampFit] = {}
        for key, levels in lamps.items():
            if progress:
                progress(key)
            before = await io.read_lux()
            if before is None:
                raise CalibrationAborted(f"before {key}: no illuminance reading")
            if await io.presence_on():
                _warn_once(warnings, f"presence detected during the measurement of {key}")
            readings = []
            for pct in levels:
                await io.set_lamp(key, pct)
                await io.sleep(settle_s)
                readings.append(await io.read_lux() or 0.0)
            await io.set_lamp(key, None)
            await io.sleep(settle_s)
            after = await io.read_lux() or 0.0
            fit = fit_lamp(key, levels, tuple(readings), before, after)
            if fit.lux[2] - fit.base < MIN_GROWTH:
                raise CalibrationAborted(f"{key}: the lamp does not change the illuminance (no growth)")
            if await io.presence_on():
                _warn_once(warnings, f"presence detected during the measurement of {key}")
            fits[key] = fit
        return CalibrationResult(bg0, fits, warnings)
    finally:
        await io.restore()


def _warn_once(warnings: list[str], text: str) -> None:
    if text not in warnings:
        warnings.append(text)


def open_k_candidate(lux: float, daylight: float) -> float:
    """light_open_calibrate: lux / daylight clamped to the helper range, rounded to 0.01."""
    return round(min(max(lux / (daylight or 1), 0.05), 20), 2)


def levels_for(key: str) -> tuple[int, int, int]:
    return LEVELS.get(key, DEFAULT_LEVELS)


# ---- applying candidates (pure) -------------------------------------------------

K_RANGE = (0.001, 100.0)
DARK_RANGE = (0.0, 20.0)


class CandidateError(ValueError):
    pass


def validate_candidates(cand: dict[str, float], known_keys) -> dict[str, float]:
    """Return clamped k per known lamp plus 'bg0'. Raises when nothing usable is there."""
    if not cand or "bg0" not in cand:
        raise CandidateError("no calibration candidates; run the calibration first")
    out = {}
    for key in known_keys:
        if key in cand:
            lo, hi = K_RANGE
            out[key] = round(min(max(cand[key], lo), hi), 3)
    if not out:
        raise CandidateError("candidates contain none of the configured lamps")
    out["bg0"] = round(min(max(cand["bg0"], DARK_RANGE[0]), DARK_RANGE[1]), 2)
    return out


NO_SENSOR_REASON = "the zone has no illuminance sensor (calibration measures lux)"


def open_k_blockers(*, mode: str | None, daylight: float | None, any_lamp_on: bool, lux: float | None,
                    has_sensor: bool = True) -> str | None:
    """Reason why light_open_calibrate must not run now, or None."""
    if not has_sensor:
        return NO_SENSOR_REASON
    if mode != "Day":
        return "mode is not Day"
    if daylight is None or daylight < 40:
        return "daylight factor below 40"
    if any_lamp_on:
        return "a lamp is on"
    if lux is None:
        return "no lux reading"
    return None


def summary_message(result: CalibrationResult) -> str:
    lines = [f"background {result.bg0} lx"]
    lines += [f"WARNING: {w}" for w in result.warnings]
    for k, f in result.fits.items():
        w = f"  ({'; '.join(f.warnings)})" if f.warnings else ""
        lines.append(f"{k}: k={f.k_fit} (lux {f.lux[0]}/{f.lux[1]}/{f.lux[2]}, base {round(f.base, 1)}){w}")
    return "\n".join(lines)

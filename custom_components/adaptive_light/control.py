"""When to turn a zone's lamps on or off. No brightness, color, or transition."""
from __future__ import annotations


def clock_minutes(value: str) -> int | None:
    """``HH:MM`` or ``HH:MM:SS`` as minutes from midnight."""
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def in_night_window(hour: int, minute: int, start: str, end: str) -> bool:
    """True when the local time sits in ``[start, end)``. A window may cross midnight."""
    now_m = hour * 60 + minute
    start_m = clock_minutes(start)
    end_m = clock_minutes(end)
    if start_m is None or end_m is None or start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= now_m < end_m
    return now_m >= start_m or now_m < end_m


def light_estimate(room_lux: float | None, room_coefficient: float, radiation: float | None, outdoor_factor: float) -> float | None:
    """One source. A room sensor wins; otherwise outdoor radiation. Never both."""
    if room_lux is not None:
        return room_lux * room_coefficient
    if radiation is not None:
        return radiation * outdoor_factor
    return None


LUX_ON = 40.0
LUX_OFF = 80.0
LUX_ON_HOLD = 120.0
LUX_OFF_HOLD = 600.0


def lux_gate(
    estimate: float | None,
    threshold: float,
    now: float,
    below_since: float | None,
    above_since: float | None,
    seen: bool,
) -> tuple[bool, bool, float | None, float | None, bool]:
    """Return ``(on_ready, morning_off, below_since, above_since, seen)``.

    Threshold 0, or no numeric estimate, ignores light. Otherwise the lamps turn on
    only below 40 lx and the morning turns them off only above 80 lx. The band in
    between does not change the lamps. A later dip below 40 must last 2 minutes, and
    a rise above 80 must last 10 minutes. The first sample below 40 is already dark
    enough, so a save can turn the lamps on at once.
    """
    if threshold <= 0 or estimate is None:
        return True, False, None, None, seen
    if estimate < LUX_ON:
        if below_since is None:
            below_since = now if seen else now - LUX_ON_HOLD
        return now - below_since >= LUX_ON_HOLD, False, below_since, None, True
    if estimate > LUX_OFF:
        if above_since is None:
            above_since = now
        return False, now - above_since >= LUX_OFF_HOLD, None, above_since, True
    return False, False, None, None, True


def decide(occupied: bool, off_since: float | None, now: float, timeout: float) -> tuple[str, float | None]:
    """Return ``(command, off_since)``.

    ``on`` when any presence sensor is on. ``off`` when every sensor has been off for
    at least ``timeout`` seconds. ``wait`` until that moment. The timeout is measured
    from the instant they all became off.
    """
    if occupied:
        return "on", None
    since = now if off_since is None else off_since
    if now - since >= timeout:
        return "off", None
    return "wait", since

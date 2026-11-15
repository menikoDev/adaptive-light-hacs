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


def light_allows_on(estimate: float | None, threshold: float) -> bool:
    """Presence may turn lamps on when there is no estimate, the threshold is 0, or the room is darker than the threshold."""
    if threshold <= 0 or estimate is None:
        return True
    return estimate < threshold


def morning_off(estimate: float | None, threshold: float) -> bool:
    """Lamps that are already on go off once the estimate rises above the threshold. Threshold 0 never does this."""
    if threshold <= 0 or estimate is None:
        return False
    return estimate > threshold


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

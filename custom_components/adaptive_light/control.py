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


LUX_AVERAGE_S = 600.0


def lower_lux(upper: float, gap: float) -> float:
    """Lower threshold is the upper threshold minus the gap. A gap of 0 keeps them equal."""
    if gap <= 0:
        return upper
    return max(0.0, upper - gap)


def allows_on(estimate: float | None, upper: float) -> bool:
    """Turn on from the current reading. Threshold 0, or no reading, does not block."""
    if upper <= 0 or estimate is None:
        return True
    return estimate < upper


def window_average(samples: list[tuple[float, float]], now: float, window: float = LUX_AVERAGE_S) -> float | None:
    """Time-weighted mean of the last ``window`` seconds.

    ``None`` until the samples cover that whole span, so a short spike cannot decide the off.
    Each sample holds until the next one.
    """
    if not samples or window <= 0:
        return None
    start = now - window
    if samples[0][0] > start:
        return None
    index = 0
    while index + 1 < len(samples) and samples[index + 1][0] <= start:
        index += 1
    total = 0.0
    covered = 0.0
    moment = start
    value = samples[index][1]
    index += 1
    while index < len(samples) and samples[index][0] < now:
        nxt = samples[index][0]
        total += value * (nxt - moment)
        covered += nxt - moment
        moment = nxt
        value = samples[index][1]
        index += 1
    if now > moment:
        total += value * (now - moment)
        covered += now - moment
    if covered <= 0:
        return None
    return total / covered


def average_recheck(samples: list[tuple[float, float]], now: float, lower: float, window: float = LUX_AVERAGE_S) -> float | None:
    """Seconds until the 10-minute average should be read again. ``None`` when it will not move."""
    if not samples:
        return None
    if samples[0][0] > now - window:
        return samples[0][0] + window - now
    average = window_average(samples, now, window)
    if average is None or average < lower:
        return None
    start = now - window
    edge = samples[0][1]
    nxt = None
    for stamp, value in samples:
        if stamp <= start:
            edge = value
        elif nxt is None:
            nxt = stamp
    current = samples[-1][1]
    bound = window if nxt is None else nxt + window - now
    slope = (current - edge) / window
    if slope < 0:
        cross = (lower - average) / slope
        if 0 < cross <= bound:
            return cross
    return bound if bound > 0 else None


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

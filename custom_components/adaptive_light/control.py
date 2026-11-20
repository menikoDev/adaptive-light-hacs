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


DEFAULT_WINDOW_MIN = 10
DEFAULT_WINDOW_S = 600.0


def weighted_average(samples: list[tuple[float, float]], now: float, window: float = DEFAULT_WINDOW_S) -> float | None:
    """Recency-weighted mean over ``window`` seconds. Newer moments weigh more.

    A sample holds until the next one. An instant ``age`` seconds before ``now`` has weight
    ``window - age``, so the newest moment weighs the full window and the far edge weighs nothing.
    A history shorter than the window is averaged over the span that exists. One sample is itself.
    """
    if not samples or window <= 0:
        return None
    usable = [(stamp, value) for stamp, value in samples if stamp <= now]
    if not usable:
        return None
    start = now - window
    index = 0
    while index + 1 < len(usable) and usable[index + 1][0] <= start:
        index += 1
    if usable[index][0] > start:
        moment = usable[index][0]
        value = usable[index][1]
        index += 1
    else:
        moment = start
        value = usable[index][1]
        index += 1
    total = 0.0
    weight_sum = 0.0

    def accumulate(held: float, begin: float, end: float) -> None:
        nonlocal total, weight_sum
        if end <= begin:
            return
        # weight(t) = t - start. Integral over [begin, end] is half the difference of squares.
        gained = 0.5 * ((end - start) ** 2 - (begin - start) ** 2)
        if gained <= 0:
            return
        total += held * gained
        weight_sum += gained

    while index < len(usable) and usable[index][0] < now:
        nxt = usable[index][0]
        accumulate(value, moment, nxt)
        moment = nxt
        value = usable[index][1]
        index += 1
    if now > moment:
        accumulate(value, moment, now)
    if weight_sum <= 0:
        return usable[-1][1]
    return total / weight_sum


def next_average_change(
    samples: list[tuple[float, float]], now: float, threshold: float, window: float = DEFAULT_WINDOW_S,
) -> float | None:
    """Seconds until the weighted average crosses ``threshold``. ``None`` when it will not."""
    if not samples or window <= 0 or threshold <= 0:
        return None
    current = weighted_average(samples, now, window)
    if current is None:
        return None
    below = current < threshold
    step = 1.0 if window <= 3600 else 30.0
    previous = 0.0
    dt = step
    while dt <= window + 1e-9:
        future = weighted_average(samples, now + dt, window)
        if future is not None and (future < threshold) != below:
            lo, hi = previous, dt
            for _ in range(16):
                mid = (lo + hi) / 2
                mid_value = weighted_average(samples, now + mid, window)
                if mid_value is not None and (mid_value < threshold) != below:
                    hi = mid
                else:
                    lo = mid
            return hi if hi > 0 else step
        previous = dt
        dt += step
    return None


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

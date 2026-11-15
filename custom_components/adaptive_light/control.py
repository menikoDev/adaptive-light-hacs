"""When to turn a zone's lamps on or off. No brightness, color, or transition."""
from __future__ import annotations


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

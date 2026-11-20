"""One log line per state change or lamp action.

The Home Assistant logger namespace is ``custom_components.adaptive_light.events``.
Each record is a single line: timestamp, zone, entity, old, new, reason.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

LOGGER_NAME = "custom_components.adaptive_light.events"
_LOG = logging.getLogger(LOGGER_NAME)


def emit(zone: str, entity: str, old, new, reason: str, ts: float, **facts) -> None:
    """Write one reviewable event at info. Values do not contain spaces.

    Extra facts (service, rgb, kelvin, brightness, presence, estimate, average,
    thresholds, owner) are appended. They stay at info: the Logs page hides them
    until the full log is downloaded.
    """
    tail = "".join(f" {key}={_field(value)}" for key, value in facts.items())
    _LOG.info(
        "ts=%s zone=%s entity=%s old=%s new=%s reason=%s%s",
        _stamp(ts),
        _field(zone),
        _field(entity),
        _field(old),
        _field(new),
        _field(reason),
        tail,
    )


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _field(value) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace(" ", "_")

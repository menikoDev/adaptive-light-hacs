"""Persisted runtime state as pure data (no Home Assistant imports).

Independent of the configuration model: it only knows lamp *keys* (strings), so it keeps working whatever
the final config flow looks like. ``HassStateStore`` is the thin adapter over homeassistant.helpers.storage.Store;
tests use ``MemoryStore``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

STORE_VERSION = 2
SAVE_DELAY_S = 5.0


def _num(v, default, lo=-math.inf, hi=math.inf):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return min(max(float(v), lo), hi)


@dataclass
class PersistedState:
    """Runtime state that must survive a restart. Lamp and zone keys are plain strings."""

    pause_until: dict[str, float] = field(default_factory=dict)          # lamp key -> timestamp
    openness: dict[str, tuple[float, int]] = field(default_factory=dict)  # zone key -> (value, low_count)
    last_set_ts: float = 0.0
    enabled: bool = False
    apply: bool = False
    scenarios: bool = True                                               # behaviour scenarios (executors) on/off
    auto: dict[str, bool] = field(default_factory=dict)                  # lamp key -> switch

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "pause_until": {k: v for k, v in self.pause_until.items() if v},
            "openness": {z: {"value": v, "low_count": c} for z, (v, c) in self.openness.items()},
            "last_set_ts": self.last_set_ts,
            "enabled": self.enabled,
            "apply": self.apply,
            "scenarios": self.scenarios,
            "auto": dict(self.auto),
        }

    @classmethod
    def from_dict(cls, data: Any, *, now_ts: float, lamps: list[str] | None = None, zones: list[str] | None = None):
        """Tolerant loader: wrong types are dropped, expired pauses ignored, keys that are no longer configured
        (removed lamp or zone) are dropped so they cannot leave stale state behind."""
        s = cls()
        if not isinstance(data, dict):
            return s
        pauses = data.get("pause_until")
        if isinstance(pauses, dict):
            for k, v in pauses.items():
                t = _num(v, 0.0)
                if t > now_ts and (lamps is None or k in lamps):
                    s.pause_until[k] = t
        op = data.get("openness")
        if isinstance(op, dict):
            for z, d in op.items():
                if isinstance(d, dict) and (zones is None or z in zones):
                    s.openness[z] = (_num(d.get("value"), 100.0, 0.0, 100.0), int(_num(d.get("low_count"), 0, 0, 1000)))
        s.last_set_ts = _num(data.get("last_set_ts"), 0.0)
        s.enabled = data.get("enabled") is True
        s.apply = data.get("apply") is True
        s.scenarios = data.get("scenarios") is not False
        au = data.get("auto")
        if isinstance(au, dict):
            s.auto = {k: v is True for k, v in au.items() if isinstance(k, str) and (lamps is None or k in lamps)}
        return s


class StateStore(Protocol):
    async def async_load(self) -> Any: ...
    def async_delay_save(self, data_func, delay: float) -> None: ...
    async def async_save(self, data: Any) -> None: ...


class MemoryStore:
    """Test double with the same surface as the HA Store adapter; ``delay_save`` just records the last payload."""

    def __init__(self, initial: Any = None):
        self.data = initial
        self.saves = 0

    async def async_load(self):
        return self.data

    def async_delay_save(self, data_func, delay: float = SAVE_DELAY_S) -> None:
        self.data = data_func()
        self.saves += 1

    async def async_save(self, data):
        self.data = data
        self.saves += 1


class HassStateStore:
    """homeassistant.helpers.storage.Store wrapper (storage key per config entry). Not exercised by unit tests."""

    def __init__(self, hass, entry_id: str):
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORE_VERSION, f"adaptive_light.{entry_id}")

    async def async_load(self):
        return await self._store.async_load()

    def async_delay_save(self, data_func, delay: float = SAVE_DELAY_S) -> None:
        self._store.async_delay_save(data_func, delay)

    async def async_save(self, data):
        await self._store.async_save(data)

    async def async_remove(self):
        await self._store.async_remove()

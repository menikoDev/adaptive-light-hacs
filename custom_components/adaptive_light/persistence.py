"""Which zones are enabled, and which lamps were switched off by hand.

Older runtime state (openness, pauses, coefficients) is ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

STORE_VERSION = 3
SAVE_DELAY_S = 5.0
# Flags saved before this epoch could be set by a repeated "off" state, not a hand switch.
MANUAL_OFF_EPOCH = 2


@dataclass
class PersistedState:
    enabled: dict[str, bool] = field(default_factory=dict)
    manual_off: set[str] = field(default_factory=set)
    night_hold: set[str] = field(default_factory=set)
    manual_off_epoch: int = 0
    manual_clear: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "enabled": {k: v is True for k, v in self.enabled.items()},
            "manual_off": sorted(self.manual_off),
            "night_hold": sorted(self.night_hold),
            "manual_off_epoch": MANUAL_OFF_EPOCH,
            "manual_clear": {key: value for key, value in sorted(self.manual_clear.items())},
        }

    @classmethod
    def from_dict(cls, data: Any, *, zones: list[str] | None = None, lamps: list[str] | None = None) -> "PersistedState":
        state = cls()
        if not isinstance(data, dict):
            return state
        enabled = data.get("enabled")
        if isinstance(enabled, dict):
            for key, value in enabled.items():
                if isinstance(key, str) and (zones is None or key in zones):
                    state.enabled[key] = value is True
        manual = data.get("manual_off")
        if isinstance(manual, list):
            state.manual_off = {
                item for item in manual
                if isinstance(item, str) and (lamps is None or item in lamps)
            }
        held = data.get("night_hold")
        if isinstance(held, list):
            state.night_hold = {item for item in held if isinstance(item, str) and (zones is None or item in zones)}
        epoch = data.get("manual_off_epoch")
        if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 0:
            state.manual_off_epoch = epoch
        clears = data.get("manual_clear")
        if isinstance(clears, dict):
            for key, value in clears.items():
                if isinstance(key, str) and (zones is None or key in zones) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    state.manual_clear[key] = value
        return state


class StateStore(Protocol):
    async def async_load(self) -> Any: ...
    def async_delay_save(self, data_func, delay: float) -> None: ...
    async def async_save(self, data: Any) -> None: ...


class MemoryStore:
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
    def __init__(self, hass, entry_id: str):
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORE_VERSION, f"adaptive_light.{entry_id}")

    async def async_load(self):
        return await self._store.async_load()

    def async_delay_save(self, data_func, delay: float = SAVE_DELAY_S) -> None:
        self._store.async_delay_save(data_func, delay)

    async def async_save(self, data):
        await self._store.async_save(data)

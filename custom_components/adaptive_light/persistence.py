"""Which zones are enabled. Older runtime state (openness, pauses, coefficients) is ignored."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

STORE_VERSION = 3
SAVE_DELAY_S = 5.0


@dataclass
class PersistedState:
    enabled: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"version": STORE_VERSION, "enabled": {k: v is True for k, v in self.enabled.items()}}

    @classmethod
    def from_dict(cls, data: Any, *, zones: list[str] | None = None) -> "PersistedState":
        state = cls()
        if not isinstance(data, dict) or not isinstance(data.get("enabled"), dict):
            return state
        for key, value in data["enabled"].items():
            if isinstance(key, str) and (zones is None or key in zones):
                state.enabled[key] = value is True
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

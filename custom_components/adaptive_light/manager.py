"""Turn a zone's lamps on when any presence sensor is on, and off after the timeout."""
from __future__ import annotations

import time
from typing import Any, Callable

from .control import decide
from .model import Config
from .persistence import PersistedState

_SERVICE = {"on": "turn_on", "off": "turn_off"}


class AdaptiveLightManager:
    def __init__(self, hass, raw: dict, store=None, now: Callable[[], float] | None = None) -> None:
        self.hass = hass
        self.cfg = Config.from_dict(raw)
        self.store = store
        self._now = now or time.time
        self.enabled: dict[str, bool] = {zone: True for zone in self.cfg.zone_order}
        self._off_since: dict[str, float] = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._timers: dict[str, Callable[[], None]] = {}

    def now(self) -> float:
        return self._now()

    def add_listener(self, fn: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn()

    def is_on(self, entity: str) -> bool:
        state = self.hass.states.get(entity)
        return state is not None and state.state == "on"

    def occupied(self, zone: str) -> bool:
        return any(self.is_on(entity) for entity in self.cfg.zones[zone]["presence"])

    async def async_load(self) -> None:
        if self.store is None:
            return
        saved = PersistedState.from_dict(await self.store.async_load(), zones=self.cfg.zone_order)
        self.enabled.update(saved.enabled)

    def save(self) -> None:
        if self.store is None:
            return
        self.store.async_delay_save(lambda: PersistedState(dict(self.enabled)).to_dict())

    async def apply(self, zone: str) -> str:
        """Apply the presence rule. Service data is only the lamp entity ids."""
        if zone not in self.cfg.zones:
            return "skip"
        if not self.enabled.get(zone, True):
            self._off_since.pop(zone, None)
            self._notify()
            return "disabled"
        spec = self.cfg.zones[zone]
        command, since = decide(self.occupied(zone), self._off_since.get(zone), self.now(), float(spec["off_timeout"]))
        if since is None:
            self._off_since.pop(zone, None)
        else:
            self._off_since[zone] = since
        if command in _SERVICE:
            await self._set_lamps(spec["lamps"], command)
        self._notify()
        return command

    async def _set_lamps(self, lamps: list[str], want: str) -> None:
        need = [entity for entity in lamps if not self._already(entity, want)]
        if not need:
            return
        await self.hass.services.async_call("light", _SERVICE[want], {"entity_id": need})

    def _already(self, entity: str, want: str) -> bool:
        state = self.hass.states.get(entity)
        return state is not None and state.state == want

    def cancel_timer(self, zone: str) -> None:
        cancel = self._timers.pop(zone, None)
        if cancel is not None:
            cancel()

    def _arm(self, zone: str) -> None:
        from homeassistant.helpers.event import async_call_later

        self.cancel_timer(zone)
        since = self._off_since.get(zone)
        if since is None or not self.enabled.get(zone, True):
            return
        delay = float(self.cfg.zones[zone]["off_timeout"]) - (self.now() - since)
        if delay <= 0:
            return

        async def _fire(_now: Any) -> None:
            self._timers.pop(zone, None)
            await self.apply(zone)
            self._arm(zone)

        self._timers[zone] = async_call_later(self.hass, delay, _fire)

    async def _on_event(self, event) -> None:
        entity = event.data.get("entity_id")
        for zone, spec in self.cfg.zones.items():
            if entity in spec["presence"]:
                await self.apply(zone)
                self._arm(zone)

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_state_change_event

        entities = sorted({entity for spec in self.cfg.zones.values() for entity in spec["presence"]})
        if entities:
            self._unsubs.append(async_track_state_change_event(self.hass, entities, self._on_event))
        for zone in self.cfg.zone_order:
            await self.apply(zone)
            self._arm(zone)

    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for zone in list(self._timers):
            self.cancel_timer(zone)

    async def async_flush(self) -> None:
        if self.store is not None:
            await self.store.async_save(PersistedState(dict(self.enabled)).to_dict())

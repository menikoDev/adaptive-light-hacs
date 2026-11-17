"""Turn a zone's lamps on when any presence sensor is on, and off after the timeout.

A lamp switched off by hand stays off until somebody else switches it on. The zone's own
timeout ``turn_off`` does not count as a hand switch.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .const import DOMAIN
from .control import LUX_OFF_HOLD, LUX_ON_HOLD, decide, in_night_window, lux_gate
from .model import Config, outdoor_block
from .persistence import MANUAL_OFF_EPOCH, PersistedState

_SERVICE = {"on": "turn_on", "off": "turn_off"}


class AdaptiveLightManager:
    def __init__(self, hass, raw: dict, store=None, now: Callable[[], float] | None = None, local_now: Callable | None = None) -> None:
        self.hass = hass
        self.cfg = Config.from_dict(raw)
        self.store = store
        self._now = now or time.time
        self._local_now = local_now
        self.enabled: dict[str, bool] = {zone: True for zone in self.cfg.zone_order}
        self.manual_off: set[str] = set()
        self._night_hold: set[str] = set()
        self._last_motion: dict[str, float] = {}
        self._seen: dict[str, bool] = {}
        self._off_since: dict[str, float] = {}
        self._below_since: dict[str, float] = {}
        self._above_since: dict[str, float] = {}
        self._lux_seen: set[str] = set()
        self._manual_clear: dict[str, int] = {}
        self._expect: dict[str, str] = {}
        self._own_contexts: set[str] = set()
        self._lamp_seen: dict[str, str] = {}
        self._lamps = {entity for spec in self.cfg.zones.values() for entity in spec["lamps"]}
        self._remember_lamps()
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

    def estimate(self, zone: str) -> float | None:
        """Lux from one source: the zone sensor, or outdoor radiation when the zone has none."""
        spec = self.cfg.zones.get(zone)
        if spec is None:
            return None
        if spec.get("lux"):
            room = _reading(self.hass, spec["lux"])
            if room is None:
                return None
            return room * float(spec.get("lux_coefficient") or 1)
        entity, factor = controller_outdoor(self.hass)
        if not entity:
            return None
        radiation = _reading(self.hass, entity)
        if radiation is None:
            return None
        return radiation * factor

    async def async_load(self) -> None:
        if self.store is None:
            return
        saved = PersistedState.from_dict(
            await self.store.async_load(), zones=self.cfg.zone_order, lamps=sorted(self._lamps)
        )
        self.enabled.update(saved.enabled)
        self.manual_off = set(saved.manual_off)
        self._night_hold = set(saved.night_hold)
        if saved.manual_off_epoch < MANUAL_OFF_EPOCH:
            self.manual_off.clear()
        cleared = False
        for zone, spec in self.cfg.zones.items():
            generation = spec.get("manual_clear") or 0
            known = saved.manual_clear.get(zone, 0)
            if isinstance(generation, int) and generation > known:
                for lamp in spec["lamps"]:
                    if lamp in self.manual_off:
                        cleared = True
                    self.manual_off.discard(lamp)
            self._manual_clear[zone] = max(generation if isinstance(generation, int) else 0, known)
        if cleared:
            self.save()

    def save(self) -> None:
        if self.store is None:
            return
        self.store.async_delay_save(self._snapshot)

    def _snapshot(self) -> dict:
        return PersistedState(
            dict(self.enabled), set(self.manual_off), set(self._night_hold), manual_clear=dict(self._manual_clear),
        ).to_dict()

    async def apply(self, zone: str) -> str:
        """Apply the presence rule. Service data is only the lamp entity ids."""
        if zone not in self.cfg.zones:
            return "skip"
        if not self.enabled.get(zone, True):
            self._off_since.pop(zone, None)
            self._notify()
            return "disabled"
        spec = self.cfg.zones[zone]
        if not spec.get("night"):
            self._night_hold.discard(zone)
        rising = self._rising(zone)
        if rising:
            self._last_motion[zone] = self.now()
            if zone in self._night_hold:
                self._night_hold.discard(zone)
                self.save()
        if self._night_quiet(zone, spec):
            self._off_since.pop(zone, None)
            await self._set_lamps(spec["lamps"], "off")
            self._notify()
            return "night"
        level = self.estimate(zone)
        threshold = float(spec.get("threshold", 50))
        on_ready, off_ready, below, above, seen = lux_gate(
            level, threshold, self.now(),
            self._below_since.get(zone), self._above_since.get(zone), zone in self._lux_seen,
        )
        if below is None:
            self._below_since.pop(zone, None)
        else:
            self._below_since[zone] = below
        if above is None:
            self._above_since.pop(zone, None)
        else:
            self._above_since[zone] = above
        if seen:
            self._lux_seen.add(zone)
        if self.occupied(zone) and off_ready:
            self._off_since.pop(zone, None)
            await self._set_lamps(spec["lamps"], "off")
            self._notify()
            return "lux"
        command, since = decide(self.occupied(zone), self._off_since.get(zone), self.now(), float(spec["off_timeout"]))
        if command == "on" and not on_ready:
            command, since = "wait", None
        if since is None:
            self._off_since.pop(zone, None)
        else:
            self._off_since[zone] = since
        if command in _SERVICE:
            await self._set_lamps(spec["lamps"], command)
        self._notify()
        return command

    def _rising(self, zone: str) -> bool:
        edge = False
        for entity in self.cfg.zones[zone]["presence"]:
            on = self.is_on(entity)
            if on and self._seen.get(entity) is False:
                edge = True
            self._seen[entity] = on
        return edge

    def _in_night(self, spec: dict) -> bool:
        if not spec.get("night"):
            return False
        hour, minute = self._clock()
        return in_night_window(hour, minute, spec["night_start"], spec["night_end"])

    def _clock(self) -> tuple[int, int]:
        if self._local_now is not None:
            current = self._local_now()
            return current.hour, current.minute
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tzname = getattr(getattr(self.hass, "config", None), "time_zone", None) or "UTC"
        try:
            tz = ZoneInfo(str(tzname))
        except Exception:
            tz = ZoneInfo("UTC")
        current = datetime.now(tz)
        return current.hour, current.minute

    def _night_quiet(self, zone: str, spec: dict) -> bool:
        """Hold the lamps off after an hour without new movement inside the night window.

        A presence sensor that stays on does not count as movement. The next rising edge
        clears the hold. Outside the window the hold, once started, still waits for movement
        so the lamps do not come back on just because morning arrived.
        """
        if zone in self._night_hold:
            return True
        if not self._in_night(spec) or not self.occupied(zone):
            return False
        last = self._last_motion.get(zone)
        if last is None:
            self._last_motion[zone] = self.now()
            return False
        if self.now() - last < float(spec["night_still"]) * 60:
            return False
        self._night_hold.add(zone)
        self.save()
        return True

    async def _set_lamps(self, lamps: list[str], want: str) -> None:
        need = []
        for entity in lamps:
            if self._already(entity, want):
                continue
            if want == "on" and entity in self.manual_off:
                continue
            need.append(entity)
        if not need:
            return
        for entity in need:
            self._expect[entity] = want
        context = self._own_context()
        kwargs = {"context": context} if context is not None else {}
        await self.hass.services.async_call("light", _SERVICE[want], {"entity_id": need}, **kwargs)

    def _own_context(self):
        try:
            from homeassistant.core import Context
        except ImportError:
            return None
        context = Context()
        self._own_contexts.add(context.id)
        return context

    def _remember_lamps(self) -> None:
        for entity in self._lamps:
            state = self.hass.states.get(entity)
            if state is not None and state.state in ("on", "off"):
                self._lamp_seen[entity] = state.state

    def note_lamp_change(self, entity: str, new_state: str, *, caused_by_us: bool | None = None) -> None:
        """Record a lamp state change.

        A change the zone itself requested does not touch the manual-off flag. A hand
        switch is only an ``on`` to ``off`` change. A repeated ``off`` while the lamp
        was already off does not set the flag, and any other ``on`` clears it.
        """
        if entity not in self._lamps or new_state not in ("on", "off"):
            return
        previous = self._lamp_seen.get(entity)
        self._lamp_seen[entity] = new_state
        if caused_by_us is None:
            caused_by_us = self._expect.get(entity) == new_state
        if self._expect.get(entity) == new_state:
            self._expect.pop(entity, None)
        if caused_by_us:
            return
        before = set(self.manual_off)
        if new_state == "off" and previous == "on":
            self.manual_off.add(entity)
        elif new_state == "on":
            self.manual_off.discard(entity)
        self._expect.pop(entity, None)
        if self.manual_off != before:
            self.save()

    def _caused_by_us(self, state) -> bool | None:
        ctx = getattr(state, "context", None)
        if ctx is None:
            return None
        cid = getattr(ctx, "id", None)
        parent = getattr(ctx, "parent_id", None)
        if cid in self._own_contexts or (parent and parent in self._own_contexts):
            return True
        return None

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
        if not self.enabled.get(zone, True):
            return
        delay = self._pending_delay(zone)
        if delay is None or delay <= 0:
            return

        async def _fire(_now: Any) -> None:
            self._timers.pop(zone, None)
            await self.apply(zone)
            self._arm(zone)

        self._timers[zone] = async_call_later(self.hass, delay, _fire)

    def _pending_delay(self, zone: str) -> float | None:
        spec = self.cfg.zones[zone]
        delays: list[float] = []
        since = self._off_since.get(zone)
        if since is not None:
            delays.append(float(spec["off_timeout"]) - (self.now() - since))
        if zone not in self._night_hold and self._in_night(spec) and self.occupied(zone):
            last = self._last_motion.get(zone)
            if last is not None:
                delays.append(float(spec["night_still"]) * 60 - (self.now() - last))
        below = self._below_since.get(zone)
        if below is not None:
            delays.append(LUX_ON_HOLD - (self.now() - below))
        above = self._above_since.get(zone)
        if above is not None:
            delays.append(LUX_OFF_HOLD - (self.now() - above))
        pending = [item for item in delays if item > 0]
        return min(pending) if pending else None

    async def _on_event(self, event) -> None:
        entity = event.data.get("entity_id")
        new = event.data.get("new_state")
        if entity in self._lamps and new is not None:
            self.note_lamp_change(entity, new.state, caused_by_us=self._caused_by_us(new))
        radiation, _factor = controller_outdoor(self.hass)
        for zone, spec in self.cfg.zones.items():
            if entity in spec["presence"] or entity == spec.get("lux") or (radiation and entity == radiation):
                await self.apply(zone)
                self._arm(zone)

    def _watched(self) -> set[str]:
        entities = set(self._lamps)
        for spec in self.cfg.zones.values():
            entities.update(spec["presence"])
            if spec.get("lux"):
                entities.add(spec["lux"])
        radiation, _factor = controller_outdoor(self.hass)
        if radiation:
            entities.add(radiation)
        return entities

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_state_change_event

        self._remember_lamps()
        entities = sorted(self._watched())
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
            await self.store.async_save(self._snapshot())


def _reading(hass, entity: str) -> float | None:
    if not entity:
        return None
    state = hass.states.get(entity)
    raw = getattr(state, "state", None) if state is not None else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    if isinstance(raw, str) and raw.strip().lower() in ("", "unknown", "unavailable", "none", "on", "off"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def controller_outdoor(hass) -> tuple[str, float]:
    """Radiation sensor and factor shared by every zone. Tests may set hass.outdoor."""
    direct = getattr(hass, "outdoor", None)
    if isinstance(direct, dict):
        block = outdoor_block(direct)
        return block["radiation"], float(block["factor"])
    getter = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if not callable(getter):
        return "", 0.6
    radiation, factor = "", 0.6
    try:
        found = getter(DOMAIN)
    except Exception:
        return "", 0.6
    for entry in found or []:
        options = getattr(entry, "options", None) or {}
        data = getattr(entry, "data", None) or {}
        raw = options.get("config") if isinstance(options, dict) else None
        if not isinstance(raw, dict) and isinstance(data, dict):
            raw = data.get("config")
        if not isinstance(raw, dict) or not isinstance(raw.get("outdoor"), dict):
            continue
        block = outdoor_block(raw["outdoor"])
        radiation, factor = block["radiation"], float(block["factor"])
    return radiation, factor

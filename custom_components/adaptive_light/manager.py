"""Turn a zone's lamps on when any presence sensor is on, and off after the timeout.

A lamp switched off by hand stays off until somebody else switches it on. The zone's own
timeout ``turn_off`` does not count as a hand switch.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .const import DOMAIN
from .control import decide, in_night_window, next_average_change, weighted_average
from .events import emit
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
        self._lux_samples: dict[str, list[tuple[float, float]]] = {}
        self._manual_clear: dict[str, int] = {}
        self._expect: dict[str, str] = {}
        self._own_contexts: set[str] = set()
        self._lamp_seen: dict[str, str] = {}
        self._log_state: dict[tuple[str, str], str] = {}
        self._decision: dict[str, str] = {}
        self._decision_reason: dict[str, str] = {}
        self._recheck: set[str] = set()
        self._block_logged: set[str] = set()
        self._lux_hold: set[str] = set()
        self._released: set[str] = set()
        self._apply_stack: set[str] = set()
        self._again: set[str] = set()
        self._hold_sent: dict[str, float] = {}
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
                        self._emit(zone, lamp, "manual-off", "clear", "save-recheck")
                    self.manual_off.discard(lamp)
                    self._block_logged.discard(lamp)
                    self._share_manual(lamp, False)
                self._recheck.add(zone)
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
        """Apply one zone, then the zone that just became owner of a shared lamp."""
        if zone in self._apply_stack:
            self._again.add(zone)
            return "reenter"
        self._apply_stack.add(zone)
        try:
            result = await self._apply_zone(zone)
            if zone in self.cfg.zones:
                await self._handoff(zone)
            while self._again:
                nxt = self._again.pop()
                if nxt in self._apply_stack:
                    continue
                self._apply_stack.add(nxt)
                await self._apply_zone(nxt)
                if nxt in self.cfg.zones:
                    await self._handoff(nxt)
            return result
        finally:
            self._apply_stack.clear()
            self._again.clear()

    async def _apply_zone(self, zone: str) -> str:
        """Apply the presence rule. Only the owner calls the light service."""
        if zone not in self.cfg.zones:
            return "skip"
        spec = self.cfg.zones[zone]
        self._log_enabled(zone)
        self._log_occupied(zone)
        self._lux_hold.discard(zone)
        if not self.enabled.get(zone, True):
            self._log_decision(zone, "disabled", "disabled")
            self._off_since.pop(zone, None)
            self._notify()
            return "disabled"
        if not self._in_active_hours(spec):
            # Outside its hours the zone does not turn lamps on. Ownership is refused
            # in _owner_of, so a shared lamp goes to a zone that is inside its hours.
            self._log_decision(zone, "wait", "outside-hours")
            self._notify()
            return "wait"
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
            self._log_decision(zone, "off", "night-mode")
            await self._set_lamps(zone, spec["lamps"], "off", "night-mode")
            self._notify()
            return "night"
        level = self.estimate(zone)
        threshold = float(spec.get("threshold", 50))
        window_min = float(spec.get("window") or 10)
        window = window_min * 60.0
        self._log_estimate(zone, spec, level)
        self._log_thresholds(zone, threshold, window_min)
        if threshold > 0 and level is not None:
            self._note_lux(zone, level, window)
        average = weighted_average(self._lux_samples.get(zone, []), self.now(), window) if threshold > 0 else None
        occupied = self.occupied(zone)
        if occupied:
            self._released.discard(zone)
        # A finished off-timeout must not start again on a lux tick. That wait used to
        # look like presence and let a dark refrigerator steal a shared lamp.
        if not occupied and zone in self._released:
            command, since = "wait", None
            timed_out = False
        else:
            command, since = decide(occupied, self._off_since.get(zone), self.now(), float(spec["off_timeout"]))
            timed_out = command == "off"
        if command == "on" and threshold > 0 and average is not None:
            if average > threshold:
                command, since = "off", None
                self._lux_hold.add(zone)
            elif not average < threshold:
                command, since = "wait", None
        if timed_out:
            self._released.add(zone)
        if since is None:
            self._off_since.pop(zone, None)
        else:
            self._off_since[zone] = since
        # A finished timeout answers "wait" so a lux tick cannot look like presence.
        # That same answer used to keep a lit shared lamp on when this idle zone
        # became the owner again: the previous zone had already let go, and nobody
        # called turn_off.
        handed_back = False
        if command == "wait" and not occupied and zone in self._released:
            if any(self._owns(zone, lamp) and self.is_on(lamp) for lamp in spec["lamps"]):
                command = "off"
                handed_back = True
                self._off_since.pop(zone, None)
        if command == "on":
            reason = self._on_reason(zone, threshold, average)
        elif handed_back:
            reason = "hand-back"
        elif command == "off" and zone in self._lux_hold:
            reason = "average-above"
        elif command == "off":
            reason = "timeout"
        elif not occupied:
            reason = "no-presence"
        else:
            reason = "average-at-threshold"
        self._recheck.discard(zone)
        self._log_decision(zone, command if command in _SERVICE else "wait", reason)
        if command in _SERVICE:
            await self._set_lamps(zone, spec["lamps"], command, reason)
        self._notify()
        return command

    def _bus(self) -> dict:
        bus = getattr(self.hass, "_adaptive_light_bus", None)
        if not isinstance(bus, dict):
            bus = {"contexts": set(), "owners": {}}
            setattr(self.hass, "_adaptive_light_bus", bus)
        return bus

    def _peers(self) -> list["AdaptiveLightManager"]:
        peers: list[AdaptiveLightManager] = []
        data = getattr(self.hass, "data", None)
        if isinstance(data, dict):
            bucket = data.get(DOMAIN)
            if isinstance(bucket, dict):
                for mgr in bucket.values():
                    if isinstance(mgr, AdaptiveLightManager):
                        peers.append(mgr)
        if all(mgr is not self for mgr in peers):
            peers.append(self)
        return peers

    def _share_manual(self, entity: str, blocked: bool) -> None:
        for mgr in self._peers():
            if entity not in mgr._lamps:
                continue
            before = entity in mgr.manual_off
            if blocked:
                mgr.manual_off.add(entity)
            else:
                mgr.manual_off.discard(entity)
                mgr._block_logged.discard(entity)
            if mgr is not self and before != (entity in mgr.manual_off):
                mgr.save()

    def _manual_blocked(self, entity: str) -> bool:
        return any(entity in mgr.manual_off for mgr in self._peers() if entity in mgr._lamps or mgr is self)

    def _timeout_pending(self, zone: str) -> bool:
        since = self._off_since.get(zone)
        if since is None or zone not in self.cfg.zones:
            return False
        return self.now() - since < float(self.cfg.zones[zone]["off_timeout"])

    def _in_active_hours(self, spec: dict) -> bool:
        """Empty or equal times mean the zone is always active. Night mode is a different window."""
        start = spec.get("active_start") or ""
        end = spec.get("active_end") or ""
        if not start or not end or start == end:
            return True
        hour, minute = self._clock()
        return in_night_window(hour, minute, start, end)

    def is_active(self, zone: str) -> bool:
        """Enabled, inside its hours, and presence, a running off timeout, night hold, or a lux hold."""
        if zone not in self.cfg.zones or not self.enabled.get(zone, True):
            return False
        if not self._in_active_hours(self.cfg.zones[zone]):
            return False
        return (
            self.occupied(zone)
            or zone in self._night_hold
            or self._timeout_pending(zone)
            or zone in self._lux_hold
        )

    def _owner_of(self, lamp: str):
        rows = []
        for mgr in self._peers():
            for zone, spec in mgr.cfg.zones.items():
                if lamp not in spec["lamps"] or not mgr.enabled.get(zone, True):
                    continue
                if not mgr._in_active_hours(spec):
                    continue
                rows.append((int(spec.get("priority") or 0), zone, mgr, mgr.is_active(zone)))
        if not rows:
            return None
        active = [row for row in rows if row[3]]
        pool = active or rows
        pool.sort(key=lambda row: (-row[0], row[1]))
        _rank, zone, mgr, _active = pool[0]
        return mgr, zone

    def _zone_spec(self, ident) -> dict | None:
        if not isinstance(ident, tuple) or len(ident) != 2:
            return None
        mgr_id, zone = ident
        for mgr in self._peers():
            if id(mgr) == mgr_id:
                return mgr.cfg.zones.get(zone)
        return None

    def _owns(self, zone: str, lamp: str) -> bool:
        found = self._owner_of(lamp)
        return found is not None and found[0] is self and found[1] == zone

    async def _handoff(self, zone: str) -> None:
        spec = self.cfg.zones.get(zone)
        if spec is None:
            return
        owners = self._bus()["owners"]
        for lamp in spec["lamps"]:
            found = self._owner_of(lamp)
            ident = None if found is None else (id(found[0]), found[1])
            seen = lamp in owners
            prev = owners.get(lamp)
            if seen and prev == ident:
                continue
            owners[lamp] = ident
            old = prev[1] if isinstance(prev, tuple) else "-"
            new = ident[1] if ident is not None else "-"
            if old != new:
                self._emit(new if new != "-" else old, lamp, old, new, "owner")
            # The zone that let go may have locked the color in Adaptive Lighting.
            # The zone that receives the lamp does not hold color, so that lock has
            # to come off. Otherwise the lamp stays the previous RGB.
            if (
                found is not None
                and _sends_color(self._zone_spec(prev))
                and not _sends_color(found[0].cfg.zones.get(found[1]))
            ):
                await self._mark_adaptive_lighting([lamp], False)
            if found is None or (found[0] is self and found[1] == zone):
                continue
            if found[0] is self:
                self._again.add(found[1])
            else:
                await found[0].apply(found[1])

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

    async def _set_lamps(self, zone: str, lamps: list[str], want: str, reason: str) -> None:
        spec = self.cfg.zones.get(zone) or {}
        color_fields = _service_color(spec) if want == "on" else {}
        need = []
        for entity in lamps:
            if not self._owns(zone, entity):
                continue
            current = self._lamp_state(entity)
            # An already-correct lamp with no color or brightness stays untouched.
            # A configured color or brightness is sent again so a color-temperature
            # lamp does not remain at whatever Adaptive Lighting last wrote.
            if self._already(entity, want) and not color_fields:
                continue
            facts = self._service_facts(zone, entity, want, color_fields)
            if want == "on" and self._manual_blocked(entity):
                if entity not in self._block_logged:
                    self._emit(zone, entity, current, "on", "manual-off-block", **facts)
                    self._block_logged.add(entity)
                continue
            self._block_logged.discard(entity)
            self._emit(zone, entity, current, want, reason, **facts)
            need.append(entity)
        if not need:
            return
        for entity in need:
            for mgr in self._peers():
                if entity in mgr._lamps:
                    mgr._expect[entity] = want
        context = self._own_context()
        kwargs = {"context": context} if context is not None else {}
        manual = _manual_control(color_fields) if want == "on" else None
        if manual is not None:
            # Mark the lamps before turn_on. Adaptive Lighting adapts an off→on
            # event and would otherwise replace a held color or brightness.
            await self._mark_adaptive_lighting(need, manual)
        data: dict[str, Any] = {"entity_id": need, **color_fields}
        await self.hass.services.async_call("light", _SERVICE[want], data, **kwargs)
        if want == "on" and "brightness" in color_fields:
            # A combined brightness and color call is dropped by some lamps: the
            # light turns on, and the color never lands. Color goes last.
            color_only = {key: value for key, value in color_fields.items() if key != "brightness"}
            if color_only:
                await self.hass.services.async_call(
                    "light", "turn_on", {"entity_id": need, **color_only}, **kwargs
                )
        if want == "off" and _manual_control(_service_color(spec)) is not None:
            released = [entity for entity in need if self._lamp_state(entity) == "off"]
            await self._mark_adaptive_lighting(released, False)

    def _own_context(self):
        try:
            from homeassistant.core import Context
        except ImportError:
            return None
        context = Context()
        self._own_contexts.add(context.id)
        self._bus()["contexts"].add(context.id)
        return context

    def _note_lux(self, zone: str, level: float, window: float) -> None:
        samples = self._lux_samples.setdefault(zone, [])
        now = self.now()
        if samples and samples[-1][0] == now:
            samples[-1] = (now, level)
        else:
            samples.append((now, level))
        start = now - window
        keep = 0
        for index, (stamp, _value) in enumerate(samples):
            if stamp <= start:
                keep = index
            else:
                break
        if keep:
            del samples[:keep]

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
        matched = any(mgr._expect.get(entity) == new_state for mgr in self._peers())
        if caused_by_us is None:
            caused_by_us = matched
        if matched:
            for mgr in self._peers():
                if mgr._expect.get(entity) == new_state:
                    mgr._expect.pop(entity, None)
        if caused_by_us:
            return
        before = set(self.manual_off)
        zone = self._zone_of(entity)
        if new_state == "off" and previous == "on":
            self._share_manual(entity, True)
            self._emit(zone, entity, "clear", "manual-off", "manual-off")
        elif new_state == "on":
            if self._manual_blocked(entity):
                self._emit(zone, entity, "manual-off", "clear", "manual-off")
            self._share_manual(entity, False)
            self._block_logged.discard(entity)
        self._expect.pop(entity, None)
        if self.manual_off != before:
            self.save()

    def _caused_by_us(self, state) -> bool | None:
        ctx = getattr(state, "context", None)
        if ctx is None:
            return None
        cid = getattr(ctx, "id", None)
        parent = getattr(ctx, "parent_id", None)
        shared = self._bus()["contexts"]
        if cid in self._own_contexts or cid in shared or (parent and (parent in self._own_contexts or parent in shared)):
            return True
        return None

    def _already(self, entity: str, want: str) -> bool:
        state = self.hass.states.get(entity)
        return state is not None and state.state == want

    def _lamp_state(self, entity: str) -> str:
        state = self.hass.states.get(entity)
        raw = getattr(state, "state", None) if state is not None else None
        return raw if raw in ("on", "off") else "-"

    def _zone_of(self, entity: str) -> str:
        for zone, spec in self.cfg.zones.items():
            if entity in spec["lamps"]:
                return zone
        return ""

    def _emit(self, zone: str, entity: str, old, new, reason: str, **facts) -> None:
        emit(zone, entity, old, new, reason, self.now(), **facts)

    def _service_facts(self, zone: str, entity: str, want: str, data: dict) -> dict:
        spec = self.cfg.zones.get(zone) or {}
        threshold = int(spec.get("threshold") or 0)
        window_min = int(spec.get("window") or 10)
        level = self.estimate(zone) if zone in self.cfg.zones else None
        average = (
            weighted_average(self._lux_samples.get(zone, []), self.now(), window_min * 60.0)
            if threshold > 0 else None
        )
        found = self._owner_of(entity)
        rgb = data.get("rgb_color")
        if isinstance(rgb, list):
            rgb = ",".join(str(part) for part in rgb)
        elif "color_name" in data:
            rgb = data["color_name"]
        return {
            "service": "turn_on" if want == "on" else "turn_off",
            "rgb": rgb,
            "kelvin": data.get("color_temp_kelvin"),
            "brightness": data.get("brightness"),
            "presence": "on" if zone in self.cfg.zones and self.occupied(zone) else "off",
            "estimate": _logged_number(level),
            "average": None if average is None else _logged_number(round(float(average), 2)),
            "threshold": threshold,
            "window": window_min,
            "owner": found[1] if found else None,
        }

    def _log_value(self, zone: str, kind: str, entity: str, new: str, reason: str) -> None:
        key = (zone, kind)
        if key in self._log_state and self._log_state[key] == new:
            return
        old = self._log_state.get(key, "-")
        self._log_state[key] = new
        self._emit(zone, entity, old, new, reason)

    def _log_enabled(self, zone: str) -> None:
        new = "on" if self.enabled.get(zone, True) else "off"
        self._log_value(zone, "enabled", f"switch.{zone}_enabled", new, "enabled")

    def _log_occupied(self, zone: str) -> None:
        new = "on" if self.occupied(zone) else "off"
        self._log_value(zone, "occupied", f"binary_sensor.{zone}_occupied", new, "occupied")

    def _log_estimate(self, zone: str, spec: dict, level: float | None) -> None:
        new = "-" if level is None else f"{float(level):g}"
        self._log_value(zone, "estimate", spec.get("lux") or "estimate", new, "estimate")

    def _log_thresholds(self, zone: str, threshold: float, window_min: float) -> None:
        self._log_value(zone, "thresholds", zone, f"threshold={threshold:g},window={window_min:g}", "thresholds")

    def _log_decision(self, zone: str, new: str, reason: str) -> None:
        if self._decision.get(zone) == new and self._decision_reason.get(zone) == reason:
            return
        old = self._decision.get(zone, "-")
        self._decision[zone] = new
        self._decision_reason[zone] = reason
        self._emit(zone, zone, old, new, reason)

    def _on_reason(self, zone: str, threshold: float, average: float | None) -> str:
        if zone in self._recheck:
            return "save-recheck"
        if threshold > 0 and average is not None and average < threshold:
            return "average-below"
        return "presence"

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
        if float(spec.get("threshold") or 0) > 0:
            samples = self._lux_samples.get(zone) or []
            window = float(spec.get("window") or 10) * 60.0
            delay = next_average_change(samples, self.now(), float(spec.get("threshold") or 0), window)
            if delay is not None:
                delays.append(delay)
        pending = [item for item in delays if item > 0]
        return min(pending) if pending else None

    async def _on_event(self, event) -> None:
        entity = event.data.get("entity_id")
        new = event.data.get("new_state")
        if entity in self._lamps and new is not None:
            state = getattr(new, "state", None)
            caused = self._caused_by_us(new)
            self.note_lamp_change(entity, state, caused_by_us=caused)
            if state == "off" and caused:
                await self._mark_adaptive_lighting([entity], False)
            elif state == "on" and not caused:
                await self._rehold(entity)
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

    async def _mark_adaptive_lighting(self, lamps: list[str], manual: bool | str) -> None:
        """Ask Adaptive Lighting not to adapt one attribute, or to take the lamp back.

        ``manual`` is ``"color"``, ``"brightness"``, or True for both. False clears
        every mark. A held color that is empty does not reach this method: there is
        nothing to protect, and the call would force Adaptive Lighting to adapt.
        Clearing happens only after the lamp is already off, so that service cannot
        turn it back on. The switch's own settings are left alone.
        """
        if not lamps:
            return
        services = getattr(self.hass, "services", None)
        has = getattr(services, "has_service", None)
        if not callable(has) or not has("adaptive_lighting", "set_manual_control"):
            return
        try:
            await services.async_call(
                "adaptive_lighting",
                "set_manual_control",
                {"lights": list(lamps), "manual_control": manual},
            )
        except Exception:
            return

    async def _rehold(self, entity: str) -> None:
        """Send the zone color again when something else repaints a lamp we still want on."""
        found = self._owner_of(entity)
        if found is None or found[0] is not self:
            return
        _mgr, zone = found
        if zone not in self.cfg.zones or not self.is_active(zone):
            return
        fields = _service_color(self.cfg.zones[zone])
        if not fields or _color_matches(self.hass, entity, fields):
            return
        last = self._hold_sent.get(entity)
        if last is not None and self.now() - last < 1:
            return
        self._hold_sent[entity] = self.now()
        await self.apply(zone)


def _logged_number(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _color_matches(hass, entity: str, fields: dict) -> bool:
    """True when the lamp already shows the color and brightness we would send."""
    state = hass.states.get(entity)
    attrs = getattr(state, "attributes", None) or {}
    if "color_temp_kelvin" in fields:
        current = attrs.get("color_temp_kelvin")
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            return False
        if int(current) != int(fields["color_temp_kelvin"]):
            return False
    if "rgb_color" in fields:
        current = attrs.get("rgb_color")
        if tuple(current or ()) != tuple(fields["rgb_color"]):
            return False
    if "color_name" in fields:
        return False
    if "brightness" in fields:
        current = attrs.get("brightness")
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            return False
        if int(current) != int(fields["brightness"]):
            return False
    return True


def _service_color(spec: dict) -> dict[str, Any]:
    """Held color and brightness for turn_on. RGB wins. Kelvin is used when RGB is empty."""
    data: dict[str, Any] = {}
    if spec.get("hold_color") is True:
        # A chosen RGB is the color. Kelvin is only used when red, green, and blue are all empty.
        chosen = _color_data(spec.get("color"))
        if chosen:
            data.update(chosen)
        else:
            kelvin = spec.get("color_temp")
            if isinstance(kelvin, int) and not isinstance(kelvin, bool):
                data["color_temp_kelvin"] = kelvin
    if spec.get("hold_brightness") is True:
        bright = spec.get("brightness")
        if isinstance(bright, int) and not isinstance(bright, bool):
            data["brightness"] = bright
    return data


def _sends_color(spec: dict | None) -> bool:
    """True when this zone would send an RGB, a color name, or kelvin."""
    if not spec:
        return False
    fields = _service_color(spec)
    return any(key in fields for key in ("rgb_color", "color_name", "color_temp_kelvin"))


def _manual_control(fields: dict) -> bool | str | None:
    """Adaptive Lighting ``manual_control`` value for the fields we actually send.

    One call has to carry every mark: a later call replaces the earlier one.
    True marks both color and brightness. An empty held color adds no field and
    does not ask Adaptive Lighting to do anything.
    """
    color = any(key in fields for key in ("color_temp_kelvin", "rgb_color", "color_name"))
    bright = "brightness" in fields
    if color and bright:
        return True
    if color:
        return "color"
    if bright:
        return "brightness"
    return None


def _color_data(color: Any) -> dict[str, Any]:
    """Service fields for a chosen color. Empty sends nothing, so Adaptive Lighting keeps the lamp."""
    if isinstance(color, str) and color.strip():
        return {"color_name": color.strip()}
    if (
        isinstance(color, list)
        and len(color) == 3
        and all(isinstance(part, int) and not isinstance(part, bool) and 0 <= part <= 255 for part in color)
    ):
        return {"rgb_color": list(color)}
    return {}


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

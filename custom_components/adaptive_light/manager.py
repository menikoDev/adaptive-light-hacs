"""Runtime glue between Home Assistant and core.py / executors.py for any number of zones and lamps.

Uses only hass.states / hass.services, so tests can drive it with a fake hass.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from . import core, params as P
from .executors import Bind, Ctx
from .model import Config, normalise, validate
from .persistence import PersistedState, SAVE_DELAY_S

_LOGGER = logging.getLogger(__name__)
_BAD = (None, "unknown", "unavailable", "")
MEDIA_ACTIVE = ("on", "playing", "paused", "buffering")


def _num(state) -> float | None:
    if state is None or state.state in _BAD:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class AdaptiveLightManager:
    def __init__(self, hass, cfg: dict | Config, *, store=None, now: Callable[[], datetime] | None = None,
                 persist_config: Callable[[dict], None] | None = None, sleep=None):
        self.hass = hass
        self.store = store
        self._now = now or (lambda: datetime.now().astimezone())
        self._persist_config = persist_config
        self.sleep = sleep or asyncio.sleep
        self.state = PersistedState()
        self.values: dict[str, Any] = {"daylight_factor": None, "zones": {}, "lamps": {}}
        self.openness: dict[str, core.OpennessState] = {}
        self._listeners: list[Callable[[], None]] = []
        self._own_context_ids: set[str] = set()
        self._unsub: list[Callable[[], None]] = []
        self.executors = None
        self.calibration_tasks: dict[str, Any] = {}
        self.calibration_status: dict[str, str] = {}
        self.set_config(cfg)

    # ------------------------------------------------------------ config
    def set_config(self, cfg: dict | Config) -> None:
        self.cfg = cfg if isinstance(cfg, Config) else Config(cfg)
        self._flat_zone = {z: self.cfg.flat(z) for z in self.cfg.zone_order}
        self._flat_lamp = {k: self.cfg.flat(x["zone"], k) for k, x in self.cfg.lamps.items()}
        self._lamp_p = {k: self.cfg.lamp_params(k) for k in self.cfg.lamps}
        self._zone_p = {z: self.cfg.zone_params(z) for z in self.cfg.zone_order}
        for z in self.cfg.zone_order:
            self.openness.setdefault(z, core.OpennessState())
        for k in self.cfg.lamps:
            self.state.auto.setdefault(k, False)
            self.state.pause_until.setdefault(k, 0.0)

    def set_param(self, level: str, target: str, name: str, value: Any) -> None:
        """Change one parameter override (level: global | zone | group | lamp); value None removes the override."""
        raw = self.cfg.to_dict()
        holder = {"global": lambda: raw, "zone": lambda: next(z for z in raw["zones"] if z["key"] == target),
                  "group": lambda: next(g for g in raw["groups"] if g["key"] == target),
                  "lamp": lambda: next(x for x in raw["lamps"] if x["key"] == target)}[level]()
        d = holder.setdefault("params", {})
        if name not in P.PARAMS or level not in P.PARAMS[name].levels:
            raise ValueError(f"parameter {name} cannot be set at level {level}")
        if value is None:
            d.pop(name, None)
        else:
            d[name] = P.coerce(name, value)
        self._replace_config(raw)

    def _replace_config(self, raw: dict) -> None:
        raw = normalise(raw)
        errs = validate(raw)
        if errs:
            raise ValueError("; ".join(str(e) for e in errs))
        self.set_config(raw)
        if self._persist_config:
            self._persist_config(self.cfg.to_dict())
        self.recompute()

    # ------------------------------------------------------------ plumbing
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _st(self, entity_id: str | None):
        return self.hass.states.get(entity_id) if entity_id else None

    def mark_dirty(self) -> None:
        if self.store is not None:
            self.store.async_delay_save(self.export_state, SAVE_DELAY_S)

    def export_state(self) -> dict:
        self.state.openness = {z: (o.value, o.low_count) for z, o in self.openness.items()}
        return self.state.to_dict()

    async def async_load(self) -> None:
        if self.store is None:
            return
        data = await self.store.async_load()
        self.state = PersistedState.from_dict(
            data, now_ts=self._now().timestamp(), lamps=list(self.cfg.lamps), zones=self.cfg.zone_order)
        for k in self.cfg.lamps:
            self.state.auto.setdefault(k, False)
            self.state.pause_until.setdefault(k, 0.0)
        for z, (v, c) in self.state.openness.items():
            self.openness[z] = core.OpennessState(v, c)

    async def async_flush(self) -> None:
        if self.store is not None:
            await self.store.async_save(self.export_state())

    # convenient accessors used by entities
    @property
    def enabled(self) -> bool: return self.state.enabled
    @property
    def apply(self) -> bool: return self.state.apply

    @property
    def scenarios(self) -> bool: return self.state.scenarios
    @property
    def auto(self) -> dict[str, bool]: return self.state.auto
    @property
    def pause_until(self) -> dict[str, float]: return self.state.pause_until
    @property
    def last_set_ts(self) -> float: return self.state.last_set_ts

    def set_switch(self, which: str, value: bool, lamp: str | None = None) -> None:
        if which == "auto":
            self.state.auto[lamp] = value
        elif which == "enabled":
            self.state.enabled = value
        elif which == "apply":
            self.state.apply = value
        elif which == "scenarios":
            self.state.scenarios = value
            if not value and self.executors:
                self.executors.cancel_all()
        self.mark_dirty()
        self._notify()

    # ------------------------------------------------------------ state readers
    def lamp_state(self, key: str) -> core.LampState:
        s = self._st(self.cfg.entity_of(key))
        on = s is not None and s.state == "on"
        return core.LampState(on, s.attributes.get("brightness") if on else None)

    def lamp_full(self, key: str) -> tuple[bool, int | None, int | None]:
        s = self._st(self.cfg.entity_of(key))
        on = s is not None and s.state == "on"
        return (on, s.attributes.get("brightness") if on else None, s.attributes.get("color_temp_kelvin") if on else None)

    def zone_lamps(self, zone: str) -> dict[str, core.LampState]:
        return {k: self.lamp_state(k) for k in self.cfg.lamps_in_zone(zone)}

    def _entity_state(self, ent: str | None, default: str = "") -> str:
        s = self._st(ent)
        return s.state if s is not None else default

    def lamp_mode(self, key: str) -> str:
        ent = self._lamp_p[key]["mode_entity"]
        return self._entity_state(ent, "") if ent else "Day"

    def zone_mode(self, zone: str) -> str:
        ent = self._zone_p[zone]["mode_entity"]
        return self._entity_state(ent, "") if ent else "Day"

    def daylight(self) -> int | None:
        rad = _num(self._st(self.cfg.sensor("radiation")))
        elev = _num(self._st(self.cfg.sensor("elevation")))
        return core.daylight_factor(rad, elev, float(self.cfg.global_param("radiation_full_scale")))

    def zone_lux(self, zone: str) -> float | None:
        return _num(self._st(self.cfg.sensor("illuminance", zone)))

    def inputs_for(self, zone: str, lamp: str | None = None) -> core.Inputs:
        if lamp is None:
            p, mode, scene_ent, evening_h = self._flat_zone[zone], self.zone_mode(zone), self._zone_p[zone]["scene_entity"], self._zone_p[zone]["evening_start_hour"]
        else:
            p, mode, scene_ent, evening_h = self._flat_lamp[lamp], self.lamp_mode(lamp), self._lamp_p[lamp]["scene_entity"], self._lamp_p[lamp]["evening_start_hour"]
        scene = self._entity_state(scene_ent, "") if scene_ent else None
        return core.Inputs(
            lux=self.zone_lux(zone), daylight=self.daylight(), openness=self.openness[zone].value, mode=mode,
            scene=scene, evening_quiet=self._now().hour >= int(evening_h), lamps=self.zone_lamps(zone), params=p)

    def set_calibration_status(self, zone: str, status: str) -> None:
        self.calibration_status[zone] = status
        self._notify()

    # ------------------------------------------------------------ pipeline
    def recompute(self) -> dict[str, Any]:
        v: dict[str, Any] = {"daylight_factor": self.daylight(), "zones": {}, "lamps": {}}
        for z in self.cfg.zone_order:
            i = self.inputs_for(z)
            ready = i.lux is not None and i.daylight is not None
            v["zones"][z] = {
                "ambient": core.ambient(i.lux, i.lamps, i.params) if i.lux is not None else None,
                "ambient_lamps_lx": round(core.lamps_lx(i.lamps, i.params), 1),
                "need": round(core.need("", i)) if ready else None,
                "trust": core.trust(i.daylight, i.openness, i.lux, i.lamps, i.params) if ready else None,
                "openness": self.openness[z].value,
                "low_count": self.openness[z].low_count,
                "openness_raw": core.openness_raw(i.lux, i.daylight, i.mode, i.lamps, i.params),
                "evening_quiet": i.evening_quiet,
                "mode": i.mode,
            }
            for k in self.cfg.lamps_in_zone(z):
                il = self.inputs_for(z, k)
                v["lamps"][k] = {"target": core.target(k, il) if ready else None}
        self.values = v
        self._notify()
        return v

    def target(self, lamp: str) -> int | None:
        return self.values["lamps"].get(lamp, {}).get("target")

    def openness_tick(self, reset: bool = False) -> None:
        self.recompute()
        for z in self.cfg.zone_order:
            raw = self.values["zones"][z]["openness_raw"]
            self.openness[z] = core.openness_step(self.openness[z], raw, self._flat_zone[z], reset=reset)
        self.mark_dirty()
        self.recompute()

    # ------------------------------------------------------------ controller
    async def controller_tick(self) -> list[core.LampDecision]:
        if not self.state.enabled:
            return []
        self.recompute()
        now_ts = self._now().timestamp()
        decisions = []
        step = int(self.cfg.global_param("step_pct"))
        thr = int(self.cfg.global_param("threshold_pct"))
        transition = int(self.cfg.global_param("transition_s"))
        for key in self.cfg.lamps:
            zone = self.cfg.lamps[key]["zone"]
            lp = self._lamp_p[key]
            fridge = self._st(self.cfg.sensor("fridge", zone))
            t = self.target(key)
            mode = self.lamp_mode(key)
            d = core.decide_lamp(
                key, entity=self.cfg.entity_of(key), lamp=self.lamp_state(key), target_pct=-1 if t is None else t,
                mode=mode, fridge_busy=fridge is not None and fridge.state == "on", switch_on=self.state.auto.get(key, False),
                now_ts=now_ts, pause_until_ts=self.state.pause_until.get(key, 0.0),
                modes=P.parse_modes(lp["ctrl_modes"]), fridge_block=bool(lp["fridge_block"]), step_pct=step, threshold_pct=thr)
            decisions.append(d)
            zv = self.values["zones"][zone]
            if d.act:
                if self.state.apply:
                    await self._apply(d, now_ts, transition)
                else:
                    await self._log(
                        f"{key}: now {d.now_pct}%, target {d.target}%, would set {d.new_pct}% "
                        f"(need {zv['need']}, ambient {zv['ambient']} lx, daylight {self.values['daylight_factor']}, "
                        f"trust {zv['trust']}, openness {zv['openness']}, mode {mode}).")
            elif not self.state.apply and d.entities and self._now().minute % 10 == 0:
                await self._log(f"{key}: now {d.now_pct}%, target {d.target}%, no change ({d.reason}).")
        return decisions

    async def _apply(self, d: core.LampDecision, now_ts: float, transition: int) -> None:
        self.mark_own(now_ts)
        await self.hass.services.async_call(
            "light", "turn_on", {"entity_id": d.entities, "brightness_pct": d.new_pct, "transition": transition},
            blocking=False, context=self._new_context())

    def mark_own(self, now_ts: float | None = None) -> None:
        self.state.last_set_ts = now_ts if now_ts is not None else self._now().timestamp()
        self.mark_dirty()
        self._notify()

    def _new_context(self):
        try:
            from homeassistant.core import Context
        except ImportError:
            return None
        ctx = Context()
        self._own_context_ids.add(ctx.id)
        if len(self._own_context_ids) > 200:
            self._own_context_ids.clear()
        return ctx

    async def _log(self, message: str) -> None:
        await self.hass.services.async_call(
            "logbook", "log", {"name": "Adaptive light (shadow)", "message": message}, blocking=False)

    # ------------------------------------------------------------ override
    def handle_lamp_change(self, key: str, old, new) -> float | None:
        if old is None or new is None:
            return None
        ctx = new.context
        own = ctx is not None and ctx.id in self._own_context_ids
        autos = []
        if hasattr(self.hass.states, "async_all"):
            for a in self.hass.states.async_all("automation"):
                lt = a.attributes.get("last_triggered")
                autos.append((a.attributes.get("friendly_name"), lt.timestamp() if hasattr(lt, "timestamp") else 0.0))
        until = core.override_pause_until(
            now_ts=self._now().timestamp(), last_set_ts=self.state.last_set_ts,
            parent_id_is_none=(ctx is not None and ctx.parent_id is None) and not own,
            to_state_on=new.state == "on", had_from_state=True, automations_last_triggered=autos,
            ignore_names=self.ignore_automations, switch_on=self.state.auto.get(key, False),
            minutes=self._lamp_p[key]["override_minutes"])
        if until is None:
            return None
        self.state.pause_until[key] = until
        for g in self.cfg.groups_of(key):
            if g.get("share_pause"):
                for other in g["lamps"]:
                    if self.state.auto.get(other, False):
                        self.state.pause_until[other] = max(self.state.pause_until.get(other, 0.0), until)
        self.mark_dirty()
        self._notify()
        return until

    ignore_automations: tuple[str, ...] = ("Light: set home mode",)

    def clear_pauses(self) -> None:
        for k in self.state.pause_until:
            self.state.pause_until[k] = 0.0
        self.mark_dirty()
        self.recompute()

    # ------------------------------------------------------------ executors context
    def exec_ctx(self, zone: str) -> Ctx:
        cfg = self.cfg
        keys = cfg.lamps_in_zone(zone)

        def grp(slot: str) -> tuple[str, ...]:
            g = cfg.binding(zone, slot)
            return tuple(cfg.group_lamps(g)) if g else ()

        b = Bind(top=grp("top_group"), desk=grp("desk_group"), wake=grp("wake_group"), fridge=cfg.binding(zone, "fridge_lamp"),
                 vent_left=cfg.binding(zone, "vent_left"), vent_right=cfg.binding(zone, "vent_right"),
                 green=cfg.binding(zone, "green_lamp"))
        zv = self.values["zones"].get(zone, {})
        scene_ent = self._zone_p[zone]["scene_entity"]
        return Ctx(
            auto=dict(self.state.auto), bind=b,
            lamp_params={k: {"work_hours_only": self._lamp_p[k]["work_hours_only"], "legacy_ratio": self._lamp_p[k]["legacy_ratio"]}
                         for k in keys},
            mode=self.zone_mode(zone), lux=self.zone_lux(zone), hour=self._now().hour, now_ts=self._now().timestamp(),
            last_set_ts=self.state.last_set_ts, enabled=self.state.enabled, apply=self.state.apply,
            lamps={k: self.lamp_full(k) for k in keys}, targets={k: self.target(k) for k in keys},
            scene=self._entity_state(scene_ent, "") if scene_ent else None,
            need=zv.get("need"), ambient=zv.get("ambient"), daylight=self.values.get("daylight_factor"))

    # ------------------------------------------------------------ lifecycle
    def watched_entities(self) -> list[str]:
        ents = {x["entity"] for x in self.cfg.raw["lamps"]}
        for s in self.cfg.raw["sensors"]:
            if s["role"] in ("illuminance", "radiation", "elevation"):
                ents.add(s["entity"])
        for z in self.cfg.zone_order:
            for n in ("mode_entity", "scene_entity"):
                if self._zone_p[z][n]:
                    ents.add(self._zone_p[z][n])
        for k in self.cfg.lamps:
            for n in ("mode_entity", "scene_entity"):
                if self._lamp_p[k][n]:
                    ents.add(self._lamp_p[k][n])
        return sorted(ents)

    async def async_start(self) -> None:
        from datetime import timedelta
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
        from homeassistant.util import dt as dt_util

        self._now = dt_util.now
        lamp_ents = {self.cfg.entity_of(k): k for k in self.cfg.lamps}

        @callback
        def _changed(event):
            self.recompute()

        @callback
        def _lamp(event):
            key = lamp_ents.get(event.data["entity_id"])
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if key and old is not None and new is not None:
                if (old.state == "off" and new.state == "on") or old.attributes.get("brightness") != new.attributes.get("brightness"):
                    self.handle_lamp_change(key, old, new)
            self.recompute()

        async def _tick(now):
            self.openness_tick()
            await self.controller_tick()

        self._unsub.append(async_track_state_change_event(self.hass, self.watched_entities(), _changed))
        self._unsub.append(async_track_state_change_event(self.hass, list(lamp_ents), _lamp))
        self._unsub.append(async_track_time_interval(self.hass, _tick, timedelta(minutes=1)))
        from .exec_runtime import ExecutorRuntime
        self.executors = ExecutorRuntime(self)
        self.executors.start()
        self.recompute()

    def async_stop(self) -> None:
        if self.executors:
            self.executors.stop()
            self.executors = None
        for t in self.calibration_tasks.values():
            if not t.done():
                t.cancel()
        for u in self._unsub:
            u()
        self._unsub.clear()

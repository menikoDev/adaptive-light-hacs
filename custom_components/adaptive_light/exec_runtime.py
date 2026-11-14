"""Runs the pure executors (executors.py): step runner, triggers with ``for:`` timers, concurrency modes.

Everything that touches Home Assistant lives here; the decisions are in executors.py.
Triggers and ``mode:`` (single / restart / queued) follow the plan's automations 5.2-5.12.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from . import executors as ex

_LOGGER = logging.getLogger(__name__)
TV_ACTIVE = ("on", "playing", "paused")


class StepRunner:
    """Executes a list of steps for one zone. ``snapshots`` are in memory (like the plan's scene.create)."""

    def __init__(self, mgr):
        self.mgr = mgr
        self.snaps: dict[tuple[str, str], dict[str, dict]] = {}

    async def run(self, zone: str, steps: list[ex.Step]) -> None:
        m, hass = self.mgr, self.mgr.hass
        for s in steps:
            if isinstance(s, ex.Cmd):
                ents = [m.cfg.entity_of(k) for k in s.keys]
                if not ents:
                    continue
                data = {"entity_id": ents, **{k: (list(v) if isinstance(v, tuple) else v) for k, v in s.d.items()}}
                await hass.services.async_call("light", "turn_on" if s.action == "on" else "turn_off", data,
                                               blocking=True, context=m._new_context())
            elif isinstance(s, ex.Delay):
                await m.sleep(s.seconds)
            elif isinstance(s, ex.MarkOwn):
                m.mark_own()
            elif isinstance(s, ex.Log):
                await m._log(s.message)
            elif isinstance(s, ex.Snapshot):
                snap = {}
                for k in s.keys:
                    st = hass.states.get(m.cfg.entity_of(k))
                    a = st.attributes if st is not None else {}
                    snap[k] = {"on": st is not None and st.state == "on", "brightness": a.get("brightness"),
                               "kelvin": a.get("color_temp_kelvin"), "hs": a.get("hs_color"), "mode": a.get("color_mode")}
                self.snaps[(zone, s.name)] = snap
            elif isinstance(s, ex.Restore):
                for k, v in self.snaps.get((zone, s.name), {}).items():
                    ent = m.cfg.entity_of(k)
                    if v["on"]:
                        d: dict[str, Any] = {"entity_id": ent, "transition": s.transition}
                        if v["brightness"] is not None:
                            d["brightness"] = v["brightness"]
                        if v["mode"] == "color_temp" and v["kelvin"]:
                            d["color_temp_kelvin"] = v["kelvin"]
                        elif v["hs"]:
                            d["hs_color"] = list(v["hs"])
                        await hass.services.async_call("light", "turn_on", d, blocking=True, context=m._new_context())
                    else:
                        await hass.services.async_call("light", "turn_off", {"entity_id": ent, "transition": s.transition},
                                                       blocking=True, context=m._new_context())


class ExecutorRuntime:
    def __init__(self, mgr):
        self.mgr = mgr
        self.runner = StepRunner(mgr)
        self._unsub: list[Callable[[], None]] = []
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._pending: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------ behaviours (handlers; testable without HA events)
    def _s(self, ent: str | None, default: str = "") -> str:
        st = self.mgr.hass.states.get(ent) if ent else None
        return st.state if st is not None else default

    def _on(self, zone: str, role: str) -> bool:
        return self._s(self.mgr.cfg.sensor(role, zone)) == "on"

    def tv_on(self, zone: str) -> bool:
        return any(self._s(e) in TV_ACTIVE for e in self.mgr.cfg.sensors("media", zone))

    def ctx(self, zone: str) -> ex.Ctx:
        return self.mgr.exec_ctx(zone)

    async def do_movie(self, zone: str, to_state: str, from_state: str) -> None:
        c = self.ctx(zone)
        if to_state == "Movie":
            await self.runner.run(zone, ex.movie_start(c))
        elif from_state == "Movie":
            await self.runner.run(zone, ex.movie_end(c, to_state))

    async def do_vent(self, zone: str, trigger: str, from_value: float = 0.0, to_value: float = 0.0) -> None:
        cfg = self.mgr.cfg
        c = self.ctx(zone)
        vent_on = self._s(cfg.sensor("vent_mode", zone)) == "on"
        if not ex.vent_allowed(vent_mode=vent_on, mode=c.mode, presence=self._on(zone, "presence"),
                               fridge_busy=self._on(zone, "fridge"), c=c, trigger=trigger,
                               from_value=from_value, to_value=to_value):
            return
        advice = self._s(cfg.sensor("window_advice", zone))
        steps = ex.vent_blink(c, trigger=trigger, advice=advice)
        await self.runner.run(zone, steps)
        if ex.vent_restore_needed(self.mgr.zone_mode(zone), c.mode):
            await self.runner.run(zone, [ex.Restore("window_lights_snapshot", 0)])

    async def do_evening(self, zone: str) -> None:
        c = self.ctx(zone)
        group_off = not any(c.is_on(k) for k in c.bind.top)
        if c.bind.top and ex.evening_allowed(c, presence=self._on(zone, "presence"), group_off=group_off):
            await self.runner.run(zone, ex.evening_on(c))

    async def do_desk_on(self, zone: str) -> None:
        c = self.ctx(zone)
        if c.bind.desk and ex.desk_on_allowed(c, at_table=self._on(zone, "table")):
            await self.runner.run(zone, ex.desk_on(c))

    async def do_scene(self, zone: str) -> None:
        c = self.ctx(zone)
        if c.bind.desk:
            await self.runner.run(zone, ex.scene_apply(c))

    async def do_fridge(self, zone: str) -> None:
        m = self.mgr
        c = self.ctx(zone)
        if not ex.fridge_allowed(c):
            return
        ctx0 = ex.fridge_context(c)
        await self.runner.run(zone, ex.fridge_start(c))
        fz = m.cfg.sensor("fridge", zone)
        for _ in range(ex.FRIDGE_WAIT_MAX_S):
            st = m.hass.states.get(fz)
            if st is not None and st.state == "off" and (m._now() - st.last_changed).total_seconds() >= ex.FRIDGE_LEAVE_S:
                break
            await m.sleep(1)
        await self.runner.run(zone, ex.fridge_end(self.ctx(zone), ctx0))

    async def do_night_off(self, zone: str, trigger: str) -> None:
        c = self.ctx(zone)
        if ex.night_off_allowed(c, trigger=trigger, tv_on=self.tv_on(zone), presence=self._on(zone, "presence"),
                                table=self._on(zone, "table"), carpet=self._on(zone, "carpet"),
                                entrance=self._on(zone, "entrance"), fridge=self._on(zone, "fridge")):
            await self.runner.run(zone, ex.night_off(c))

    async def do_desk_off(self, zone: str) -> None:
        c = self.ctx(zone)
        if ex.desk_off_allowed(c):
            await self.runner.run(zone, ex.desk_off(c))

    async def do_top_off(self, zone: str) -> None:
        c = self.ctx(zone)
        if ex.top_off_allowed(c, tv_on=self.tv_on(zone), presence=self._on(zone, "presence")):
            await self.runner.run(zone, ex.top_off(c))

    async def do_green(self, zone: str, trigger: str) -> None:
        c = self.ctx(zone)
        await self.runner.run(zone, ex.green_fill(c, trigger=trigger, at_desk=self._on(zone, "table")))

    async def do_wake(self, zone: str, trigger: str) -> None:
        c = self.ctx(zone)
        saved = self._s(self.mgr.cfg.sensor("color_store", zone))
        await self.runner.run(zone, ex.night_wake(c, trigger=trigger, saved_color=saved))

    # ------------------------------------------------------------ concurrency (plan's `mode:`)
    def launch(self, name: str, zone: str, coro: Callable[[], Awaitable], mode: str, max_queued: int = 5) -> None:
        key = (zone, name)
        hass = self.mgr.hass
        if not self.mgr.state.scenarios:
            return

        async def guarded():
            try:
                await coro()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the runtime alive, log with the executor name
                _LOGGER.exception("adaptive_light executor %s (zone %s) failed", name, zone)

        if mode == "single":
            t = self._tasks.get(key)
            if t and not t.done():
                return
        elif mode == "restart":
            t = self._tasks.get(key)
            if t and not t.done():
                t.cancel()
        elif mode == "queued":
            if self._pending.get(key, 0) >= max_queued:
                return
            lock = self._locks.setdefault(key, asyncio.Lock())
            self._pending[key] = self._pending.get(key, 0) + 1
            inner = guarded

            async def guarded():  # noqa: F811
                try:
                    async with lock:
                        await inner()
                finally:
                    self._pending[key] -= 1
        self._tasks[key] = hass.async_create_task(guarded())

    # ------------------------------------------------------------ triggers
    def _watch(self, ent: str | None, cb: Callable[[Any, Any], None], *, frm=None, to=None, for_s: float = 0.0,
               numeric: tuple[str, float] | None = None) -> None:
        if not ent:
            return
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_call_later, async_track_state_change_event

        timer: list[Callable[[], None] | None] = [None]

        def cancel():
            if timer[0]:
                timer[0]()
                timer[0] = None

        def num(st):
            try:
                return float(st.state)
            except (AttributeError, ValueError, TypeError):
                return None

        def matches(old, new) -> bool:
            if new is None:
                return False
            if numeric:
                kind, thr = numeric
                v, ov = num(new), num(old)
                if v is None:
                    return False
                inside = v < thr if kind == "below" else v > thr
                was_inside = ov is not None and (ov < thr if kind == "below" else ov > thr)
                return inside and not was_inside
            if old is None or old.state == new.state:
                return False
            ok_to = to is None or new.state in (to if isinstance(to, (list, tuple, set)) else [to])
            ok_from = frm is None or old.state in (frm if isinstance(frm, (list, tuple, set)) else [frm])
            return ok_to and ok_from

        def still_holds(new) -> bool:
            if new is None:
                return False
            if numeric:
                v = num(new)
                return v is not None and (v < numeric[1] if numeric[0] == "below" else v > numeric[1])
            return to is None or new.state in (to if isinstance(to, (list, tuple, set)) else [to])

        @callback
        def handler(event):
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if for_s and timer[0] and not still_holds(new):
                cancel()
            if not matches(old, new):
                return
            if for_s:
                cancel()

                @callback
                def fire(_now):
                    timer[0] = None
                    cb(old, new)

                timer[0] = async_call_later(self.mgr.hass, for_s, fire)
            else:
                cb(old, new)

        self._unsub.append(async_track_state_change_event(self.mgr.hass, [ent], handler))
        self._unsub.append(cancel)

    def start(self) -> None:
        cfg = self.mgr.cfg
        for zone in cfg.zone_order:
            if cfg.zones[zone].get("bindings"):
                self._start_zone(zone)

    def _start_zone(self, zone: str) -> None:
        cfg, L = self.mgr.cfg, self.launch
        zp = self.mgr._zone_p[zone]
        mode_e, scene_e = zp["mode_entity"], zp["scene_entity"]
        S = lambda role: cfg.sensor(role, zone)
        has = lambda slot: bool(cfg.binding(zone, slot))

        if has("top_group"):
            self._watch(mode_e, lambda o, n: L("movie", zone, lambda: self.do_movie(zone, n.state, o.state), "queued"))
        if has("vent_left") and has("vent_right"):
            self._watch(S("window_advice"), lambda o, n: L("vent", zone, lambda: self.do_vent(zone, "advice_changed"), "single"))
            for role, tid in (("window_left", "left_moved"), ("window_right", "right_moved")):
                ent = S(role)
                if ent:
                    self._watch_value(ent, lambda o, n, tid=tid: L("vent", zone, lambda: self.do_vent(
                        zone, tid, _f(o), _f(n)), "single"))
        if has("top_group"):
            ev = lambda o, n: L("evening", zone, lambda: self.do_evening(zone), "single")
            self._watch(S("presence"), ev, frm="off", to="on")
            self._watch(S("illuminance"), ev, for_s=120, numeric=("below", 25))
            self._watch(mode_e, ev, to="Evening")
        if has("desk_group"):
            dk = lambda o, n: L("desk_on", zone, lambda: self.do_desk_on(zone), "single")
            self._watch(S("table"), dk, frm="off", to="on")
            self._watch(mode_e, dk, frm=["Movie", "Away", "Night"], to=["Day", "Evening", "Movie"])
            self._watch(scene_e, lambda o, n: L("scene", zone, lambda: self.do_scene(zone), "restart"))
            self._watch(S("table"), lambda o, n: L("desk_off", zone, lambda: self.do_desk_off(zone), "restart"), frm="on", to="off", for_s=60)
            self._watch(mode_e, lambda o, n: L("desk_off", zone, lambda: self.do_desk_off(zone), "restart"), to="Away")
        if has("fridge_lamp"):
            self._watch(S("fridge"), lambda o, n: L("fridge", zone, lambda: self.do_fridge(zone), "single"), frm="off", to="on", for_s=2)
        if has("top_group") or has("desk_group"):
            self._watch(mode_e, lambda o, n: L("night_off", zone, lambda: self.do_night_off(zone, "night_start"), "restart"), to="Night", for_s=600)
            self._watch(S("sofa"), lambda o, n: L("night_off", zone, lambda: self.do_night_off(zone, "sofa_asleep"), "restart"), to="on", for_s=1200)
        if has("top_group"):
            to_ = lambda o, n: L("top_off", zone, lambda: self.do_top_off(zone), "restart")
            self._watch(S("presence"), to_, frm="on", to="off", for_s=600)
            for tv in cfg.sensors("media", zone):
                self._watch(tv, to_, to=["off", "standby"], for_s=600)
            self._watch(mode_e, to_, to="Away")
        if has("green_lamp"):
            g = lambda tid: (lambda o, n: L("green", zone, lambda: self.do_green(zone, tid), "restart"))
            self._watch(S("illuminance"), g("dark"), for_s=180, numeric=("below", 30))
            self._watch(S("illuminance"), g("bright"), for_s=300, numeric=("above", 80))
            self._watch(S("table"), g("enter"), frm="off", to="on")
            self._watch(S("table"), g("leave"), frm="on", to="off", for_s=600)
            self._watch_time(15, 0, lambda: L("green", zone, lambda: self.do_green(zone, "end"), "restart"))
        if has("wake_group"):
            w = lambda tid: (lambda o, n: L("wake", zone, lambda: self.do_wake(zone, tid), "restart"))
            self._watch(S("presence"), w("motion"), frm="off", to="on", for_s=5)
            self._watch(S("sofa"), w("left_sofa"), frm="on", to="off")
            self._watch(S("presence"), w("quiet"), frm="on", to="off", for_s=180)

    def _watch_value(self, ent: str, cb) -> None:
        """Any state change of a numeric sensor (window open percentage)."""
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_state_change_event

        @callback
        def handler(event):
            o, n = event.data.get("old_state"), event.data.get("new_state")
            if o is not None and n is not None and o.state != n.state:
                cb(o, n)

        self._unsub.append(async_track_state_change_event(self.mgr.hass, [ent], handler))

    def _watch_time(self, hour: int, minute: int, cb) -> None:
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_time_change

        @callback
        def handler(_now):
            cb()

        self._unsub.append(async_track_time_change(self.mgr.hass, handler, hour=hour, minute=minute, second=0))

    def cancel_all(self) -> None:
        for t in self._tasks.values():
            if not t.done():
                t.cancel()

    def stop(self) -> None:
        for u in self._unsub:
            u()
        self._unsub.clear()
        for t in self._tasks.values():
            if not t.done():
                t.cancel()
        self._tasks.clear()


def _f(st) -> float:
    try:
        return float(st.state)
    except (AttributeError, ValueError, TypeError):
        return 0.0

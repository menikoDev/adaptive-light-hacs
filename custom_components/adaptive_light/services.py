"""Calibration (night, 3 levels per lamp; daytime open_k) and parameter services. Zone aware."""
from __future__ import annotations

import asyncio
import logging

from . import calibration, params as P
from .manager import MEDIA_ACTIVE

_LOGGER = logging.getLogger(__name__)


class ZoneCalibrationIO:
    """CalibrationIO over a zone's real lamps and sensors."""

    def __init__(self, hass, mgr, zone: str):
        self.hass, self.mgr, self.zone = hass, mgr, zone
        self.cfg = mgr.cfg
        self._snap: dict[str, dict] = {}

    def _ent(self, key): return self.cfg.entity_of(key)

    def blockers(self) -> str | None:
        """Why a night calibration must not start (None = go): sun, mode, presence, TV/monitors."""
        s = self.hass.states
        if not self.cfg.sensor("illuminance", self.zone):
            return calibration.NO_SENSOR_REASON
        elev = self.cfg.sensor("elevation")
        if elev:
            st = s.get(elev)
            try:
                if not float(st.state) < -8:
                    return "sun is above -8 degrees"
            except (AttributeError, ValueError):
                return "no solar elevation"
        mode_ent = self.mgr._zone_p[self.zone]["mode_entity"]
        if mode_ent and self.mgr.zone_mode(self.zone) != "Night":
            return "mode is not Night"
        pres = self.cfg.sensor("presence", self.zone)
        if pres and (s.get(pres) is None or s.get(pres).state != "off"):
            return "presence detected"
        for m in self.cfg.sensors("media", self.zone):
            st = s.get(m)
            if st is not None and st.state in MEDIA_ACTIVE:
                return f"{m} is active"
        return None

    async def preconditions_ok(self) -> bool:
        return self.blockers() is None

    async def snapshot(self) -> None:
        for k in self.cfg.lamps_in_zone(self.zone):
            st = self.hass.states.get(self._ent(k))
            a = st.attributes if st is not None else {}
            self._snap[k] = {"on": st is not None and st.state == "on", "brightness": a.get("brightness"),
                             "kelvin": a.get("color_temp_kelvin"), "hs": a.get("hs_color"), "mode": a.get("color_mode")}

    async def restore(self) -> None:
        for k, v in self._snap.items():
            if v["on"]:
                d = {"entity_id": self._ent(k), "transition": 0}
                if v["brightness"] is not None:
                    d["brightness"] = v["brightness"]
                if v["mode"] == "color_temp" and v["kelvin"]:
                    d["color_temp_kelvin"] = v["kelvin"]
                elif v["hs"]:
                    d["hs_color"] = list(v["hs"])
                await self.hass.services.async_call("light", "turn_on", d, blocking=True)
            else:
                await self.hass.services.async_call("light", "turn_off", {"entity_id": self._ent(k), "transition": 0}, blocking=True)

    async def all_off(self) -> None:
        await self.hass.services.async_call(
            "light", "turn_off", {"entity_id": [self._ent(k) for k in self.cfg.lamps_in_zone(self.zone)], "transition": 0}, blocking=True)

    async def set_lamp(self, key, pct) -> None:
        if pct is None:
            await self.hass.services.async_call("light", "turn_off", {"entity_id": self._ent(key), "transition": 0}, blocking=True)
        else:
            await self.hass.services.async_call(
                "light", "turn_on", {"entity_id": self._ent(key), "brightness_pct": pct, "color_temp_kelvin": 2700, "transition": 0},
                blocking=True)

    async def read_lux(self):
        return self.mgr.zone_lux(self.zone)

    async def presence_on(self) -> bool:
        pres = self.cfg.sensor("presence", self.zone)
        st = self.hass.states.get(pres) if pres else None
        return st is not None and st.state == "on"

    async def sleep(self, seconds: float) -> None:
        await self.mgr.sleep(seconds)


def pick_zone(mgr, zone: str | None) -> str:
    z = zone or mgr.cfg.primary_zone
    if z not in mgr.cfg.zones:
        raise ValueError(f"unknown zone {z!r}; zones: {', '.join(mgr.cfg.zone_order)}")
    return z


async def _notify(hass, title: str, message: str, nid: str) -> None:
    try:
        await hass.services.async_call("persistent_notification", "create",
                                       {"title": title, "message": message, "notification_id": nid}, blocking=False)
    except Exception:  # notifications are best effort
        _LOGGER.debug("notification failed", exc_info=True)


async def async_calibrate(hass, mgr, zone: str | None = None, settle_s: float = 60, wait: bool = False):
    """Start the night calibration of one zone in the background (about 22 minutes for 5 lamps at 60 s settle).

    Returns the asyncio task (tests wait on it). Never writes lamp parameters: candidates go to the store."""
    z = pick_zone(mgr, zone)
    running = mgr.calibration_tasks.get(z)
    if running and not running.done():
        raise ValueError(f"calibration of zone {z} is already running")
    io = ZoneCalibrationIO(hass, mgr, z)
    why = io.blockers()
    if why:
        await _notify(hass, "Light calibration not started", f"Zone {z}: {why}.", f"adaptive_light_calib_{z}")
        raise ValueError(f"calibration blocked: {why}")
    lamps = {k: P.parse_levels(mgr._lamp_p[k]["calib_levels"]) for k in mgr.cfg.lamps_in_zone(z)}

    async def job():
        try:
            mgr.set_calibration_status(z, "running")
            res = await calibration.run_calibration(io, settle_s, lamps=lamps, progress=lambda k: mgr.set_calibration_status(z, f"running {k}"))
            mgr.state.candidates[z] = {"bg0": res.bg0, **res.candidates, "_ts": mgr._now().timestamp()}
            mgr.mark_dirty()
            mgr.set_calibration_status(z, "done")
            await _notify(hass, f"Light calibration: {z}", calibration.summary_message(res) +
                          "\n\nNothing applied yet: run adaptive_light.calibrate_apply.", f"adaptive_light_calib_{z}")
        except calibration.CalibrationAborted as e:
            mgr.set_calibration_status(z, "aborted")
            await _notify(hass, "Light calibration aborted", f"Zone {z}: {e}. Lamps restored, nothing written.", f"adaptive_light_calib_{z}")
        except asyncio.CancelledError:
            mgr.set_calibration_status(z, "cancelled")
            raise
        except Exception as e:  # never leave the status stuck on "running"
            _LOGGER.exception("calibration failed")
            mgr.set_calibration_status(z, f"error: {e}")

    task = hass.async_create_task(job())
    mgr.calibration_tasks[z] = task
    if wait:
        await task
    return task


async def async_calibrate_cancel(hass, mgr, zone: str | None = None) -> bool:
    z = pick_zone(mgr, zone)
    t = mgr.calibration_tasks.get(z)
    if t and not t.done():
        t.cancel()
        return True
    return False


async def async_calibrate_apply(hass, mgr, zone: str | None = None) -> dict:
    """Write the candidates into the lamp parameters (k) and the zone parameter dark_lux_offset."""
    z = pick_zone(mgr, zone)
    cand = mgr.state.candidates.get(z)
    if not cand:
        raise ValueError(f"no calibration candidates for zone {z}; run adaptive_light.calibrate first")
    try:
        ok = calibration.validate_candidates({k: v for k, v in cand.items() if k != "_ts"}, mgr.cfg.lamps_in_zone(z))
    except calibration.CandidateError as e:
        raise ValueError(str(e)) from e
    raw = mgr.cfg.to_dict()
    for x in raw["lamps"]:
        if x["key"] in ok:
            x.setdefault("params", {})["k"] = ok[x["key"]]
    for zz in raw["zones"]:
        if zz["key"] == z:
            zz.setdefault("params", {})["dark_lux_offset"] = ok["bg0"]
    mgr._replace_config(raw)
    mgr.state.candidates.pop(z, None)
    mgr.mark_dirty()
    return ok


async def async_calibrate_open_k(hass, mgr, zone: str | None = None) -> float:
    z = pick_zone(mgr, zone)
    i = mgr.inputs_for(z)
    why = calibration.open_k_blockers(mode=i.mode, daylight=i.daylight, any_lamp_on=any(s.on for s in i.lamps.values()), lux=i.lux,
                                    has_sensor=mgr.has_lux_sensor(z))
    if why:
        raise ValueError(f"conditions not met: {why}")
    k = calibration.open_k_candidate(i.lux, i.daylight)
    mgr.set_param("zone", z, "open_k", k)
    mgr.openness[z] = mgr.openness[z].__class__(100.0, 0)
    mgr.mark_dirty()
    mgr.recompute()
    return k

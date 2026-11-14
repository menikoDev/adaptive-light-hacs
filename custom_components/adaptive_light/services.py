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

    def blocker(self) -> str | None:
        """Technical reason a calibration cannot run at all (None = can run). Conditions are warnings, see ``warnings``."""
        if not self.cfg.sensor("illuminance", self.zone):
            return calibration.NO_SENSOR_REASON
        return None

    def warnings(self) -> list[str]:
        """Advisory condition warnings (sun, mode, presence, TV/monitors). They never stop the calibration."""
        s = self.hass.states
        elev = self.cfg.sensor("elevation")
        elevation = None
        if elev:
            try:
                elevation = float(s.get(elev).state)
            except (AttributeError, ValueError, TypeError):
                elevation = None
        mode_ent = self.mgr._zone_p[self.zone]["mode_entity"]
        pres = self.cfg.sensor("presence", self.zone)
        pst = s.get(pres) if pres else None
        media = [m for m in self.cfg.sensors("media", self.zone) if s.get(m) is not None and s.get(m).state in MEDIA_ACTIVE]
        return calibration.condition_warnings(
            has_elevation=bool(elev), elevation=elevation, has_mode=bool(mode_ent),
            mode=self.mgr.zone_mode(self.zone) if mode_ent else None,
            presence_on=bool(pres) and (pst is None or pst.state != "off"), active_media=media)

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

    Refuses only for technical reasons (no illuminance sensor, already running). Unfavourable conditions (daylight, mode,
    presence, TV) are logged and shown as a persistent notification, the calibration still starts: the user decides.
    The warnings are kept in ``mgr.calibration_warnings[zone]``.

    Returns the asyncio task (tests wait on it). Never writes lamp parameters: candidates go to the store."""
    z = pick_zone(mgr, zone)
    running = mgr.calibration_tasks.get(z)
    if running and not running.done():
        raise ValueError(f"calibration of zone {z} is already running")
    io = ZoneCalibrationIO(hass, mgr, z)
    why = io.blocker()
    if why:
        await _notify(hass, "Light calibration not started", f"Zone {z}: {why}.", f"adaptive_light_calib_{z}")
        raise ValueError(f"calibration cannot run: {why}")
    warns = io.warnings()
    mgr.calibration_warnings[z] = list(warns)
    if warns:
        _LOGGER.warning("calibration of zone %s starts despite: %s", z, "; ".join(warns))
    _LOGGER.warning("calibration of zone %s: %s", z, calibration.CAUTION_EN)
    start = f"Zone {z}: calibration started.\n\n"
    if warns:
        start += "Warnings:\n" + "\n".join(f"- {w}" for w in warns) + "\n\n"
    await _notify(hass, f"Light calibration started: {z}", start + calibration.CAUTION, f"adaptive_light_calib_{z}_warning")
    lamps = {k: P.parse_levels(mgr._lamp_p[k]["calib_levels"]) for k in mgr.cfg.lamps_in_zone(z)}
    base_k = {k: float(mgr.cfg.lamp_param(k, "k")) for k in lamps}
    mgr.calibration_info.pop(z, None)

    async def job():
        try:
            mgr.set_calibration_status(z, "running")
            res = await calibration.run_calibration(io, settle_s, lamps=lamps, base_k=base_k,
                                              progress=lambda k: mgr.set_calibration_status(z, f"running {k}"))
            mgr.calibration_warnings[z] = list(dict.fromkeys(warns + res.warnings))
            mgr.state.candidates[z] = res.record(mgr._now().timestamp())
            mgr.calibration_info[z] = {"source": res.source, "base_lamps": res.base_lamps,
                                       "lamp_sources": {k: f.source for k, f in res.fits.items()}}
            mgr.mark_dirty()
            mgr.set_calibration_status(z, {calibration.SOURCE_MEASURED: "done", calibration.SOURCE_MIXED: "done_mixed",
                                           calibration.SOURCE_BASE: "done_base"}[res.source])
            if res.base_lamps:
                _LOGGER.warning("calibration of zone %s: no growth for %s, base k kept (not measured)", z, ", ".join(res.base_lamps))
                await _notify(hass, f"Light calibration: base value used in {z}", calibration.base_notice(res), f"adaptive_light_calib_{z}_base")
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
    """Write the candidates into the lamp parameters (k) and the zone parameter dark_lux_offset.

    Lamps on their base k (no growth, not measured) are written explicitly with that base value; the dark_lux_offset is
    written only when the run measured something (never from a run where no lamp showed growth). The returned dict has
    the applied k per lamp and 'bg0' only when dark_lux_offset was written; ``mgr.calibration_info[zone]`` tells what was
    measured and what was base."""
    z = pick_zone(mgr, zone)
    cand = mgr.state.candidates.get(z)
    if not cand:
        raise ValueError(f"no calibration candidates for zone {z}; run adaptive_light.calibrate first")
    try:
        ok = calibration.validate_candidates({k: v for k, v in cand.items() if k != "_ts"}, mgr.cfg.lamps_in_zone(z))
    except calibration.CandidateError as e:
        raise ValueError(str(e)) from e
    meta = calibration.candidate_meta(cand, mgr.cfg.lamps_in_zone(z))
    raw = mgr.cfg.to_dict()
    for x in raw["lamps"]:
        if x["key"] in ok:
            x.setdefault("params", {})["k"] = ok[x["key"]]
    for zz in raw["zones"]:
        if zz["key"] == z and "bg0" in ok:
            zz.setdefault("params", {})["dark_lux_offset"] = ok["bg0"]
    mgr._replace_config(raw)
    mgr.state.candidates.pop(z, None)
    mgr.calibration_info[z] = {**meta, "lamp_sources": {k: (calibration.SOURCE_BASE if k in meta["base_lamps"] else calibration.SOURCE_MEASURED)
                                                        for k in ok if k != "bg0"}, "applied": True}
    mgr.mark_dirty()
    if meta["base_lamps"]:
        _LOGGER.warning("calibrate_apply zone %s: base k written for %s (not measured)%s", z, ", ".join(meta["base_lamps"]),
                        "" if "bg0" in ok else "; dark_lux_offset not written")
        text = (f"Zone {z}: applied. Lamps on the BASE k, not measured: {', '.join(meta['base_lamps'])}."
                f"{'' if 'bg0' in ok else ' dark_lux_offset was NOT written (no lamp showed growth).'}\n\n"
                f"Зона {z}: применено. Лампы на БАЗОВОМ k, не измерены: {', '.join(meta['base_lamps'])}."
                f"{'' if 'bg0' in ok else ' dark_lux_offset НЕ записан (ни одна лампа не показала прироста).'}")
        await _notify(hass, f"Light calibration applied with base values: {z}", text, f"adaptive_light_calib_{z}_applied")
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

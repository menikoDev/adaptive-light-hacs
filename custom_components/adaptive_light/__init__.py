"""Adaptive light: ambient-aware lamp brightness for several rooms (zones)."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import services as svc
from .const import CONF_CONFIG, DOMAIN, PLATFORMS, SERVICES
from .manager import AdaptiveLightManager
from .model import Config
from .persistence import HassStateStore

_LOGGER = logging.getLogger(__name__)


def entry_config(entry: ConfigEntry) -> dict:
    return entry.options.get(CONF_CONFIG) or entry.data[CONF_CONFIG]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    def persist(cfg: dict) -> None:
        hass.config_entries.async_update_entry(entry, options={CONF_CONFIG: cfg})

    mgr = AdaptiveLightManager(hass, entry_config(entry), store=HassStateStore(hass, entry.entry_id), persist_config=persist)
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mgr
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await mgr.async_start()
    entry.async_on_unload(entry.add_update_listener(_updated))
    _register_services(hass)
    return True


async def _updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only for edits made in the UI; changes the manager itself persisted (calibration, k, set_param) are no-ops."""
    mgr = hass.data[DOMAIN].get(entry.entry_id)
    if mgr is not None and Config(entry_config(entry)).to_dict() == mgr.cfg.to_dict():
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mgr = hass.data[DOMAIN][entry.entry_id]
    mgr.async_stop()
    await mgr.async_flush()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            for name in SERVICES:
                hass.services.async_remove(DOMAIN, name)
    return ok


def _mgr(hass: HomeAssistant) -> AdaptiveLightManager:
    mgrs = list(hass.data.get(DOMAIN, {}).values())
    if not mgrs:
        raise ServiceValidationError("Adaptive Light is not set up")
    return mgrs[0]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICES[0]):
        return
    zone = {vol.Optional("zone"): cv.string}

    def wrap(fn):
        async def handler(call: ServiceCall):
            try:
                return await fn(_mgr(hass), call)
            except ValueError as e:
                raise ServiceValidationError(str(e)) from e
        return handler

    async def tick(m, c):
        if c.data["openness"]:
            m.openness_tick()
        await m.controller_tick()
    async def clear(m, c): m.clear_pauses()
    async def reset(m, c): m.openness_tick(reset=True)
    async def calib(m, c): await svc.async_calibrate(hass, m, c.data.get("zone"), c.data["settle_s"], c.data["wait"])
    async def cancel(m, c): await svc.async_calibrate_cancel(hass, m, c.data.get("zone"))
    async def apply(m, c): return {"applied": await svc.async_calibrate_apply(hass, m, c.data.get("zone"))}
    async def openk(m, c): return {"open_k": await svc.async_calibrate_open_k(hass, m, c.data.get("zone"))}
    async def setp(m, c):
        m.set_param(c.data["level"], c.data.get("target", ""), c.data["name"], c.data.get("value"))

    from homeassistant.core import SupportsResponse
    defs = {
        "controller_tick": (tick, {vol.Optional("openness", default=False): cv.boolean}, SupportsResponse.NONE),
        "clear_pauses": (clear, {}, SupportsResponse.NONE),
        "openness_reset": (reset, {}, SupportsResponse.NONE),
        "calibrate": (calib, {**zone, vol.Optional("settle_s", default=60): vol.All(vol.Coerce(float), vol.Range(min=0, max=600)),
                              vol.Optional("wait", default=False): cv.boolean}, SupportsResponse.NONE),
        "calibrate_cancel": (cancel, zone, SupportsResponse.NONE),
        "calibrate_apply": (apply, zone, SupportsResponse.OPTIONAL),
        "calibrate_open_k": (openk, zone, SupportsResponse.OPTIONAL),
        "set_param": (setp, {vol.Required("level"): vol.In(["global", "zone", "group", "lamp"]), vol.Optional("target", default=""): cv.string,
                             vol.Required("name"): cv.string, vol.Optional("value"): vol.Any(None, cv.string, vol.Coerce(float), cv.boolean)},
                      SupportsResponse.NONE),
    }
    for name, (fn, schema, resp) in defs.items():
        hass.services.async_register(DOMAIN, name, wrap(fn), schema=vol.Schema(schema), supports_response=resp)

"""Adaptive Light turns lamps on and off from presence. Brightness and color belong to another integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import CONF_CONFIG, DOMAIN, PLATFORMS
from .manager import AdaptiveLightManager
from .model import Config
from .momentary import DATA_MOMENTARY, SERVICE_MOMENTARY, MomentaryActions
from .persistence import HassStateStore


def entry_config(entry: ConfigEntry) -> dict:
    return entry.options.get(CONF_CONFIG) or entry.data[CONF_CONFIG]


def _runtimes(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DATA_MOMENTARY, {})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    raw = entry_config(entry)
    mgr = AdaptiveLightManager(hass, raw, store=HassStateStore(hass, entry.entry_id))
    actions = MomentaryActions(hass, raw)
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mgr
    _runtimes(hass)[entry.entry_id] = actions
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await mgr.async_start()
    await actions.async_start()
    _ensure_service(hass)
    entry.async_on_unload(entry.add_update_listener(_updated))
    return True


def _ensure_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_MOMENTARY):
        return

    async def _handle(call: ServiceCall) -> None:
        key = call.data["action"]
        for actions in _runtimes(hass).values():
            if key in actions.actions:
                await actions.trigger(key)
                return

    hass.services.async_register(
        DOMAIN,
        SERVICE_MOMENTARY,
        _handle,
        schema=vol.Schema({vol.Required("action"): cv.string}),
    )


async def _updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    raw = entry_config(entry)
    mgr = hass.data[DOMAIN].get(entry.entry_id)
    actions = _runtimes(hass).get(entry.entry_id)
    zones_same = mgr is not None and Config.from_dict(raw).to_dict() == mgr.cfg.to_dict()
    actions_same = actions is not None and actions.same(raw)
    if zones_same and actions_same:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mgr = hass.data[DOMAIN][entry.entry_id]
    actions = _runtimes(hass).pop(entry.entry_id, None)
    # Options are already the saved config. Lamps that left this entry must
    # move now; the manager that used to own them is about to disappear.
    saved = Config.from_dict(entry_config(entry))
    coming = {entity for spec in saved.zones.values() for entity in spec["lamps"]}
    removed = set(mgr._lamps) - coming
    if mgr.cfg.zones and not any(zone in saved.zones for zone in mgr.cfg.zones):
        removed = set(mgr._lamps)
    mgr.async_stop()
    await mgr.reassign(removed)
    if actions is not None:
        actions.async_stop()
    await mgr.async_flush()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not _runtimes(hass):
            hass.services.async_remove(DOMAIN, SERVICE_MOMENTARY)
    else:
        if actions is not None:
            _runtimes(hass)[entry.entry_id] = actions
    return ok

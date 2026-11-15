"""Adaptive Light turns lamps on and off from presence. Brightness and color belong to another integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CONFIG, DOMAIN, PLATFORMS
from .manager import AdaptiveLightManager
from .model import Config
from .persistence import HassStateStore


def entry_config(entry: ConfigEntry) -> dict:
    return entry.options.get(CONF_CONFIG) or entry.data[CONF_CONFIG]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mgr = AdaptiveLightManager(hass, entry_config(entry), store=HassStateStore(hass, entry.entry_id))
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mgr
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await mgr.async_start()
    entry.async_on_unload(entry.add_update_listener(_updated))
    return True


async def _updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    mgr = hass.data[DOMAIN].get(entry.entry_id)
    if mgr is not None and Config.from_dict(entry_config(entry)).to_dict() == mgr.cfg.to_dict():
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    mgr = hass.data[DOMAIN][entry.entry_id]
    mgr.async_stop()
    await mgr.async_flush()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok

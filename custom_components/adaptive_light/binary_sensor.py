from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import DOMAIN
from .entity import AdaptiveEntity, zone_device
from .ids import prefix


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EveningQuiet(mgr, z) for z in mgr.cfg.zone_order])


class EveningQuiet(AdaptiveEntity, BinarySensorEntity):
    _attr_icon = "mdi:weather-night"

    def __init__(self, mgr, zone):
        super().__init__(mgr, "binary_sensor", f"{prefix(mgr.cfg, zone)}light_evening_quiet",
                         f"{mgr.cfg.zones[zone]['name']} light evening quiet", zone_device(mgr, zone))
        self._zone = zone

    @property
    def is_on(self): return self.mgr.values.get("zones", {}).get(self._zone, {}).get("evening_quiet")

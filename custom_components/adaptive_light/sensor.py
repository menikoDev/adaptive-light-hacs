from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import LIGHT_LUX

from .const import DOMAIN
from .entity import AdaptiveEntity, zone_device


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneEstimate(mgr, zone) for zone in mgr.cfg.zone_order])


class ZoneEstimate(AdaptiveEntity, SensorEntity):
    """The zone's light estimate in lux. One source, never a sum."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, mgr, zone: str) -> None:
        super().__init__(mgr, "sensor", f"{zone}_estimate", None, zone_device(mgr, zone))
        self._attr_has_entity_name = True
        self._attr_translation_key = "estimate"
        self._attr_name = None
        self._zone = zone

    @property
    def native_value(self) -> float | None:
        return self.mgr.estimate(self._zone)

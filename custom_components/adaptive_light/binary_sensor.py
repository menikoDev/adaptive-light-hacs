from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .const import DOMAIN
from .entity import AdaptiveEntity, zone_device


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneOccupied(mgr, zone) for zone in mgr.cfg.zone_order])


class ZoneOccupied(AdaptiveEntity, BinarySensorEntity):
    """On when any presence sensor of the zone is on."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, mgr, zone: str) -> None:
        name = mgr.cfg.zones[zone]["name"]
        super().__init__(mgr, "binary_sensor", f"{zone}_occupied", f"{name} occupied", zone_device(mgr, zone))
        self._zone = zone

    @property
    def is_on(self) -> bool:
        return self.mgr.occupied(self._zone)

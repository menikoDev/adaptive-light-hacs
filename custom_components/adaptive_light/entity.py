from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import AdaptiveLightManager


def zone_device(mgr: AdaptiveLightManager, zone: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"zone_{zone}")},
        name=f"Adaptive Light: {mgr.cfg.zones[zone]['name']}",
        manufacturer="Adaptive Light",
        model="Zone",
    )


class AdaptiveEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, mgr: AdaptiveLightManager, domain: str, object_id: str, name: str, device: DeviceInfo | None = None) -> None:
        self.mgr = mgr
        self.entity_id = f"{domain}.{object_id}"
        self._attr_unique_id = f"adaptive_light_{object_id}"
        self._attr_name = name
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.mgr.add_listener(self.async_write_ha_state))

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import AdaptiveEntity, zone_device


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZoneEnabled(mgr, zone) for zone in mgr.cfg.zone_order])


class ZoneEnabled(AdaptiveEntity, SwitchEntity):
    """Pause the zone. While off, presence does not turn its lamps on or off."""

    _attr_icon = "mdi:light-switch"

    def __init__(self, mgr, zone: str) -> None:
        name = mgr.cfg.zones[zone]["name"]
        super().__init__(mgr, "switch", f"{zone}_enabled", f"{name} enabled", zone_device(mgr, zone))
        self._zone = zone

    @property
    def is_on(self) -> bool:
        return self.mgr.enabled.get(self._zone, True)

    async def async_turn_on(self, **kwargs) -> None:
        self.mgr.enabled[self._zone] = True
        self.mgr.save()
        await self.mgr.apply(self._zone)
        self.mgr._arm(self._zone)

    async def async_turn_off(self, **kwargs) -> None:
        self.mgr.enabled[self._zone] = False
        self.mgr.cancel_timer(self._zone)
        self.mgr.save()
        await self.mgr.apply(self._zone)
        self.mgr._notify()

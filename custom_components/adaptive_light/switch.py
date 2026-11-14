from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import AdaptiveEntity, hub_device, zone_device


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    ents = [AdaptiveSwitch(mgr, "light_adaptive_enabled", "Light adaptive enabled", "enabled", None, hub_device()),
            AdaptiveSwitch(mgr, "light_adaptive_apply", "Light adaptive apply", "apply", None, hub_device()),
            AdaptiveSwitch(mgr, "light_adaptive_scenarios", "Light scenarios (movie, evening, desk, fridge ...)", "scenarios", None, hub_device())]
    ents += [AdaptiveSwitch(mgr, f"light_{k}_auto", f"Light {k} auto", "auto", k, zone_device(mgr, x["zone"]))
             for k, x in mgr.cfg.lamps.items()]
    async_add_entities(ents)


class AdaptiveSwitch(AdaptiveEntity, SwitchEntity):
    """State lives in the manager (persisted in the integration's Store), not in RestoreEntity."""

    def __init__(self, mgr, object_id, name, which, lamp, device):
        super().__init__(mgr, "switch", object_id, name, device)
        self._which, self._lamp = which, lamp

    @property
    def is_on(self) -> bool:
        return self.mgr.auto.get(self._lamp, False) if self._which == "auto" else getattr(self.mgr, self._which)

    async def async_turn_on(self, **kwargs) -> None:
        self.mgr.set_switch(self._which, True, self._lamp)

    async def async_turn_off(self, **kwargs) -> None:
        self.mgr.set_switch(self._which, False, self._lamp)

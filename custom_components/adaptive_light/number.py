from __future__ import annotations

from homeassistant.components.number import NumberMode, NumberEntity

from .const import DOMAIN
from .entity import AdaptiveEntity, zone_device


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LampK(mgr, k) for k in mgr.cfg.lamps])


class LampK(AdaptiveEntity, NumberEntity):
    """Lux per 1% of the lamp. Set by hand here or with set_param; the controller reads it."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value, _attr_native_max_value, _attr_native_step = 0.001, 100, 0.001
    _attr_native_unit_of_measurement = "lx"

    def __init__(self, mgr, key):
        super().__init__(mgr, "number", f"light_k_{key}", f"Lux per 1 percent, {key}", zone_device(mgr, mgr.cfg.lamps[key]["zone"]))
        self._key = key

    @property
    def native_value(self): return self.mgr._lamp_p[self._key]["k"]

    async def async_set_native_value(self, value: float) -> None:
        self.mgr.set_param("lamp", self._key, "k", value)

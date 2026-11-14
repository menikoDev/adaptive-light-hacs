from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass

from .const import DOMAIN
from .entity import AdaptiveEntity, hub_device, zone_device
from .ids import ZONE_SENSORS, prefix, zone_sensor_ids


async def async_setup_entry(hass, entry, async_add_entities):
    mgr = hass.data[DOMAIN][entry.entry_id]
    cfg = mgr.cfg
    ents: list = [DaylightSensor(mgr)]
    for z in cfg.zone_order:
        ids = zone_sensor_ids(cfg, z)
        for _, key, name, unit in ZONE_SENSORS:
            ents.append(ZoneSensor(mgr, z, ids[key], key, f"{cfg.zones[z]['name']} {name}", unit))
        ents.append(CalibrationSensor(mgr, z))
    for k in cfg.lamps:
        ents.append(LampTarget(mgr, k))
        ents.append(PauseSensor(mgr, k))
    async_add_entities(ents)


class DaylightSensor(AdaptiveEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, mgr):
        super().__init__(mgr, "sensor", "daylight_factor", "Daylight factor", hub_device())

    @property
    def available(self): return self.mgr.values.get("daylight_factor") is not None

    @property
    def native_value(self): return self.mgr.values.get("daylight_factor")


class ZoneSensor(AdaptiveEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, mgr, zone, object_id, vkey, name, unit):
        super().__init__(mgr, "sensor", object_id, name, zone_device(mgr, zone))
        self._zone, self._vkey = zone, vkey
        self._attr_native_unit_of_measurement = unit
        if vkey == "ambient":
            self._attr_device_class = SensorDeviceClass.ILLUMINANCE

    @property
    def _v(self): return self.mgr.values.get("zones", {}).get(self._zone, {})

    @property
    def available(self): return self._v.get(self._vkey) is not None

    @property
    def native_value(self): return self._v.get(self._vkey)

    @property
    def extra_state_attributes(self):
        if self._vkey == "ambient":
            return {"lamps_lx": self._v.get("ambient_lamps_lx"), "source": self._v.get("lux_source"), "zone": self._zone}
        if self._vkey == "openness":
            return {"low_count": self._v.get("low_count"), "zone": self._zone}
        return {"zone": self._zone}


class LampTarget(AdaptiveEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, mgr, key):
        x = mgr.cfg.lamps[key]
        super().__init__(mgr, "sensor", f"light_target_{key}", f"Light target {key}", zone_device(mgr, x["zone"]))
        self._key = key

    @property
    def available(self): return self.mgr.target(self._key) is not None

    @property
    def native_value(self): return self.mgr.target(self._key)

    @property
    def extra_state_attributes(self):
        return {"lamp": self.mgr.cfg.entity_of(self._key), "zone": self.mgr.cfg.lamps[self._key]["zone"],
                "groups": [g["key"] for g in self.mgr.cfg.groups_of(self._key)]}


class PauseSensor(AdaptiveEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, mgr, key):
        x = mgr.cfg.lamps[key]
        super().__init__(mgr, "sensor", f"light_{key}_pause_until", f"Light {key} paused until", zone_device(mgr, x["zone"]))
        self._key = key

    @property
    def native_value(self):
        ts = self.mgr.pause_until.get(self._key, 0.0)
        return datetime.fromtimestamp(ts, timezone.utc) if ts > self.mgr._now().timestamp() else None


class CalibrationSensor(AdaptiveEntity, SensorEntity):
    _attr_icon = "mdi:ruler"

    def __init__(self, mgr, zone):
        super().__init__(mgr, "sensor", f"{prefix(mgr.cfg, zone)}light_calibration", f"{mgr.cfg.zones[zone]['name']} calibration",
                         zone_device(mgr, zone))
        self._zone = zone

    @property
    def native_value(self): return self.mgr.calibration_status.get(self._zone, "idle")

    @property
    def extra_state_attributes(self):
        c = self.mgr.state.candidates.get(self._zone) or {}
        return {"candidates": {k: v for k, v in c.items() if k != "_ts"}, "zone": self._zone}

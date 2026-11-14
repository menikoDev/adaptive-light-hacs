"""Drive the setup wizard and the options flow over REST (what the UI does)."""
from ha_b import LAMPS


def wizard_two_rooms(b):
    f = b.flow_start(); fid = f["flow_id"]
    b.flow_step(fid, {"next_step_id": "global_sensors"})
    b.flow_step(fid, {"radiation": "sensor.general_graz_meteo_station_global_radiation", "elevation": "sensor.general_sun_solar_elevation"})
    b.flow_step(fid, {"name": "Living room"})
    b.flow_step(fid, {"illuminance": "sensor.living_room_presence_sensor_light_sensor_light_level", "presence": "binary_sensor.living_room_presence_sensor"})
    b.flow_step(fid, {"lamps": list(LAMPS.values())})
    b.flow_step(fid, {"next_step_id": "group_add"})
    b.flow_step(fid, {"name": "Desk", "lamps": ["work_station_lamp", "work_station_light_stick"], "share_pause": True})
    b.flow_step(fid, {"next_step_id": "more_zones"})
    b.flow_step(fid, {"next_step_id": "zone"})
    b.flow_step(fid, {"name": "Bedroom"})
    b.flow_step(fid, {"illuminance": "sensor.bedroom_lux", "presence": "binary_sensor.bedroom_presence"})
    b.flow_step(fid, {"lamps": ["light.bed_ceiling", "light.bed_night"]} if False else {"lamps": []})
    b.flow_step(fid, {"next_step_id": "more_zones"})
    r = b.flow_step(fid, {"next_step_id": "save"})
    assert r["step_id"] == "save" and not r.get("errors"), r
    return b.flow_step(fid, {})


def wizard_preset(b):
    f = b.flow_start(); fid = f["flow_id"]
    r = b.flow_step(fid, {"next_step_id": "preset_plan"})
    assert r["step_id"] == "save" and not r.get("errors"), r
    return b.flow_step(fid, {})

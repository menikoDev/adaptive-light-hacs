"""Area defaults for the new-zone step and the wizard draft (pure parts)."""
import types


from adaptive_light import areas, draft, flow_ops as ops
from adaptive_light.model import empty_config, validate

E = lambda eid, **kw: types.SimpleNamespace(entity_id=eid, **{"device_class": None, "original_device_class": None, "disabled_by": None,
                                                              "hidden_by": None, "entity_category": None, **kw})


def test_pick_defaults_sorts_entities_of_an_area_into_roles():
    entries = [
        E("light.b_lamp"), E("light.a_lamp"), E("light.group_all"), E("light.hidden", hidden_by="user"), E("light.off", disabled_by="user"),
        E("sensor.lux", original_device_class="illuminance"), E("sensor.temp", original_device_class="temperature"),
        E("sensor.diag_lux", original_device_class="illuminance", entity_category="diagnostic"),
        E("binary_sensor.motion", original_device_class="motion"), E("binary_sensor.occ", original_device_class="occupancy"),
        E("binary_sensor.door", original_device_class="door"), E("media_player.tv"), E("switch.plug"),
    ]
    attrs = lambda eid: {"entity_id": ["light.a_lamp", "light.b_lamp"]} if eid == "light.group_all" else {}
    d = areas.pick_defaults(entries, attrs)
    assert d == {"lights": ["light.a_lamp", "light.b_lamp"], "illuminance": ["sensor.lux"],
                 "presence": ["binary_sensor.occ", "binary_sensor.motion"], "media": ["media_player.tv"]}


def test_pick_defaults_falls_back_to_state_device_class_and_handles_empty_area():
    assert areas.pick_defaults([E("sensor.x")], lambda eid: {"device_class": "illuminance"})["illuminance"] == ["sensor.x"]
    assert areas.pick_defaults([]) == {"lights": [], "illuminance": [], "presence": [], "media": []}


def test_add_zone_with_key_and_area_and_unique_keys():
    cfg = empty_config()
    k1 = ops.add_zone(cfg, "Гостиная", "living_room", "living_room")
    k2 = ops.add_zone(cfg, "Living again", "living_room")                    # same key requested: made unique
    k3 = ops.add_zone(cfg, "Bedroom")
    assert (k1, k2, k3) == ("living_room", "living_room_2", "bedroom")
    z = {x["key"]: x for x in cfg["zones"]}
    assert z["living_room"]["name"] == "Гостиная" and z["living_room"]["area_id"] == "living_room"
    assert "area_id" not in z["bedroom"]
    assert validate(cfg) == []                                               # area_id is not a validation concern


def cfg_with_zone():
    cfg = empty_config(); ops.add_zone(cfg, "Hall"); ops.set_sensor(cfg, "presence", "binary_sensor.p", "hall")
    return cfg


def test_draft_round_trip_and_defensive_loading():
    cfg = cfg_with_zone()
    d = draft.unpack(draft.pack(cfg, "zone_lamps", "hall", 1000.0), now_ts=2000.0)
    assert d["step"] == "zone_lamps" and d["zone"] == "hall" and d["config"] == cfg and d["config"] is not cfg
    assert draft.unpack(draft.pack(cfg, "save", "", 1000.0), now_ts=1000.0 + draft.DRAFT_MAX_AGE_S + 1) is None          # expired
    assert draft.unpack(draft.pack(empty_config(), "zone", "", 1.0), now_ts=2.0) is None                                 # nothing entered
    for junk in (None, "x", [], {}, {"version": 99}, {"version": 1, "ts": "a", "config": {}},
                 {"version": 1, "ts": 1, "config": {"zones": "no"}}, {"version": 1, "ts": 1, "config": {"zones": [{"name": "x"}]}},
                 {"version": 1, "ts": True, "config": cfg}):
        assert draft.unpack(junk, now_ts=2.0) is None


def test_draft_resume_step_is_always_safe():
    cfg = cfg_with_zone()
    assert draft.unpack(draft.pack(cfg, "no_such_step", "hall", 1.0), 2.0)["step"] == "more_zones"
    assert draft.unpack(draft.pack(cfg, "zone_lamps", "gone", 1.0), 2.0)["step"] == "more_zones"       # zone vanished
    assert draft.unpack(draft.pack(cfg, "zone_lamps", "gone", 1.0), 2.0)["zone"] == ""
    only_sensors = empty_config(); ops.set_sensor(only_sensors, "radiation", "sensor.r")
    assert draft.unpack(draft.pack(only_sensors, "bogus", "", 1.0), 2.0)["step"] == "global_sensors"


def test_memory_draft_store():
    import asyncio
    s = draft.MemoryDraftStore()
    asyncio.run(s.async_save({"a": 1})); assert asyncio.run(s.async_load()) == {"a": 1}
    asyncio.run(s.async_remove()); assert asyncio.run(s.async_load()) is None

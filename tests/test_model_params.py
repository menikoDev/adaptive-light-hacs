import copy
import pytest

from adaptive_light import flow_ops as ops, ids, params as P
from adaptive_light.model import Config, ConfigError, empty_config, normalise, plan_living_room, validate
from helpers_cfg import two_rooms


def codes(cfg): return {e.code for e in validate(normalise(cfg))}


def test_plan_preset_and_two_rooms_are_valid():
    assert validate(plan_living_room()) == []
    assert validate(two_rooms()) == []
    assert Config(two_rooms()).primary_zone == "living_room"


def test_empty_and_broken_configs_are_rejected():
    assert "no_zones" in codes(empty_config())
    c = two_rooms(); c["sensors"] = [s for s in c["sensors"] if not (s["zone"] == "bedroom" and s["role"] == "illuminance")]
    assert not codes(c)   # the illuminance sensor is optional (0.3.0)
    c = two_rooms(); c["groups"][-1]["lamps"].append("top_left")
    assert "lamp_zone_mismatch" in codes(c)
    c = two_rooms(); c["lamps"][-1]["entity"] = c["lamps"][0]["entity"]
    assert "duplicate_lamp_entity" in codes(c)
    c = two_rooms(); c["sensors"].append({"entity": "light.x", "role": "illuminance", "zone": "bedroom"})
    assert "bad_sensor_domain" in codes(c)
    c = two_rooms(); c["zones"][1]["bindings"] = {"fridge_lamp": "top_left"}
    assert "bad_binding" in codes(c)
    c = two_rooms(); c["lamps"][-1]["zone"] = "nowhere"
    assert "unknown_zone" in codes(c)


def test_inheritance_order_lamp_group_zone_global_default():
    c = two_rooms()
    cfg = Config(c)
    assert cfg.lamp_param("bed_ceiling", "target_lux_day") == 80                       # default
    c["params"]["target_lux_day"] = 100
    assert Config(c).lamp_param("bed_ceiling", "target_lux_day") == 100                # global
    ops.set_overrides(c, "zone", "bedroom", {"target_lux_day": 60})
    assert Config(c).lamp_param("bed_ceiling", "target_lux_day") == 60                 # zone
    assert Config(c).lamp_param("top_left", "target_lux_day") == 100                   # other zone unaffected
    ops.set_overrides(c, "group", "all_bed", {"target_lux_day": 50})
    assert Config(c).lamp_param("bed_ceiling", "target_lux_day") == 50                 # group
    ops.set_overrides(c, "lamp", "bed_ceiling", {"target_lux_day": 30})
    assert Config(c).lamp_param("bed_ceiling", "target_lux_day") == 30                 # lamp wins
    assert Config(c).lamp_param("bed_night", "target_lux_day") == 50


def test_later_group_wins_and_levels_are_enforced():
    c = two_rooms()
    ops.add_group(c, "bedroom", "Second", ["bed_ceiling"])
    ops.set_overrides(c, "group", "all_bed", {"max": 70})
    ops.set_overrides(c, "group", "second", {"max": 40})
    assert Config(c).lamp_param("bed_ceiling", "max") == 40
    assert Config(c).lamp_param("bed_night", "max") == 70
    ops.set_overrides(c, "lamp", "bed_night", {"dark_lux_offset": 3, "k": 0.5, "step_pct": 20})   # zone/global-only names dropped
    assert ops.overrides(c, "lamp", "bed_night") == {"k": 0.5}


def test_flat_naming_matches_the_plan_for_the_primary_zone():
    f = Config(plan_living_room()).flat("living_room")
    for name in ("light_k_desk", "light_desk_min", "light_top_left_w_out", "light_scene_work", "light_dark_lux_offset", "light_open_k"):
        assert name in f
    assert f["light_k_top_left"] == 0.1 and f["light_desk_scene_scaled"] == 1 and f["light_top_left_scene_scaled"] == 0


def test_ops_remove_cascade_and_unique_keys():
    c = two_rooms()
    assert ops.add_lamps(c, "bedroom", ["light.bed_ceiling"]) == []                   # already known
    k = ops.add_lamps(c, "bedroom", ["light.other.bed_ceiling"] if False else ["light.bed_ceiling2"])
    assert k == ["bed_ceiling2"]
    ops.remove_lamp(c, "bed_night")
    assert "bed_night" not in c["groups"][-1]["lamps"] and validate(normalise(c)) == []
    ops.remove_zone(c, "bedroom")
    assert [z["key"] for z in c["zones"]] == ["living_room"] and not any(x["zone"] == "bedroom" for x in c["lamps"])
    assert validate(normalise(c)) == []
    ops.remove_group(c, "top")
    assert "top_group" not in c["zones"][0]["bindings"]


def test_entity_ids_primary_keeps_plan_names_other_zone_prefixed():
    e = ids.expected_entities(Config(two_rooms()))
    assert "sensor.light_need" in e["sensor"] and "sensor.living_room_ambient_lux" in e["sensor"]
    assert "sensor.bedroom_light_need" in e["sensor"] and "binary_sensor.bedroom_light_evening_quiet" in e["binary_sensor"]
    assert "switch.light_bed_ceiling_auto" in e["switch"] and "number.light_k_desk" in e["number"]
    allids = [x for v in e.values() for x in v]
    assert len(allids) == len(set(allids))


def test_param_registry_is_consistent():
    for n in P.LAMP_PARAM_NAMES + P.SHARED_NAMES + P.ZONE_NAMES + P.GLOBAL_NAMES:
        assert n in P.PARAMS
    assert P.coerce("min", 500) == 100 and P.coerce("scene_scaled", "on") == 1
    assert P.parse_modes("Day, Evening") == ("Day", "Evening") and "calib_levels" not in P.PARAMS

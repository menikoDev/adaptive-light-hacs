"""Detection rules, several presence sensors (OR), and the fridge rule's trigger.

The controller is not involved: a rule only turns lamps on (adaptive: with no brightness, so the
controller can adjust them because they are now on) and, after the delay, off. 0 does not auto-off.
A lamp whose auto switch is off is never touched.
"""
from adaptive_light import executors as ex, flow_ops as ops
from adaptive_light.model import ZONE_PRESENCE, Config, normalise, plan_living_room, validate
from helpers_cfg import two_rooms

from test_executors import ALL, cmds, ctx

FRIDGE = "binary_sensor.living_room_presence_sensor_refrigerator_zone"
ROOM = "binary_sensor.living_room_presence_sensor"
EXTRA = "binary_sensor.living_room_presence_sensor_entrance_zone"


def test_presence_is_the_or_of_every_sensor_and_one_sensor_still_counts():
    assert ex.presence_occupied({ROOM: "off", EXTRA: "on"})
    assert ex.presence_occupied({ROOM: "on", EXTRA: "off"})
    assert not ex.presence_occupied({ROOM: "off", EXTRA: "off"})
    assert not ex.presence_occupied({})
    assert ex.presence_occupied({ROOM: "on"})
    # the edge the leave-timer uses: the zone goes empty only when the last sensor clears
    before, after = {ROOM: "on", EXTRA: "off"}, {ROOM: "off", EXTRA: "off"}
    assert ex.presence_occupied(before) and not ex.presence_occupied(after)
    assert ex.presence_occupied({ROOM: "on", EXTRA: "on"}) and ex.presence_occupied({ROOM: "off", EXTRA: "on"})


def test_a_zone_that_already_has_one_presence_sensor_keeps_it():
    cfg = plan_living_room()
    had = [s["entity"] for s in cfg["sensors"] if s["role"] == "presence" and s["zone"] == "living_room"]
    assert had == [ROOM]
    ops.set_presence(cfg, "living_room", had)
    assert [s["entity"] for s in cfg["sensors"] if s["role"] == "presence" and s["zone"] == "living_room"] == [ROOM]
    ops.set_presence(cfg, "living_room", [ROOM, EXTRA])
    assert [s["entity"] for s in cfg["sensors"] if s["role"] == "presence" and s["zone"] == "living_room"] == [ROOM, EXTRA]
    assert validate(normalise(cfg)) == []


def test_adaptive_rule_turns_lamps_on_without_a_brightness_and_off_only_after_a_delay():
    auto = {k: True for k in ALL}
    on = ex.detect_on_steps(["top_left", "bottom"], auto, "adaptive")
    assert [s.action for s in cmds(on)] == ["on"] and "brightness_pct" not in cmds(on)[0].d
    assert cmds(on)[0].keys == ("top_left", "bottom")
    assert ex.detect_off_steps(["top_left", "bottom"], auto, 0) == []
    assert [s.action for s in cmds(ex.detect_off_steps(["bottom"], auto, 30))] == ["off"]
    # the auto switch still gates the lamp
    assert ex.detect_on_steps(["bottom"], {"bottom": False}, "adaptive") == []
    assert ex.detect_off_steps(["bottom"], {"bottom": False}, 30) == []
    fixed = cmds(ex.detect_on_steps(["bottom"], auto, "fixed", 40, 3000))
    assert fixed[0].d == {"brightness_pct": 40, "color_temp_kelvin": 3000}
    no_color = cmds(ex.detect_on_steps(["bottom"], auto, "fixed", 40, None))
    assert no_color[0].d == {"brightness_pct": 40}


def test_fridge_rule_watches_the_fridge_sensor_not_the_room_presence():
    rule = ops.fridge_template(plan_living_room(), "living_room")
    assert rule["sensor"] == FRIDGE and rule["lamps"] == ["bottom"]
    assert rule["brightness_pct"] == ex.FRIDGE_PCT and rule["color_temp_kelvin"] == ex.FRIDGE_KELVIN
    assert rule["off_delay_s"] == ex.FRIDGE_LEAVE_S
    cfg = plan_living_room()
    ops.add_detect_rule(cfg, "living_room", **rule)
    stored = cfg["zones"][0]["detect_rules"][0]
    assert ex.is_fridge_recipe(stored, FRIDGE)
    watches = ex.plan_detect(stored and cfg["zones"][0]["detect_rules"], fridge_entity=FRIDGE,
                             presence_entities=[ROOM, EXTRA])
    assert len(watches) == 1 and watches[0].kind == "fridge"
    assert watches[0].entities == (FRIDGE,)
    assert ROOM not in watches[0].entities and EXTRA not in watches[0].entities
    # the same numbers on the room presence are not the fridge scenario
    room = dict(stored, id="d2", sensor=ZONE_PRESENCE)
    assert not ex.is_fridge_recipe(room, FRIDGE)
    room_watch = ex.plan_detect([room], fridge_entity=FRIDGE, presence_entities=[ROOM, EXTRA])
    assert room_watch[0].zone_presence and room_watch[0].entities == (ROOM, EXTRA) and room_watch[0].kind == "onoff"
    # and the executor itself is what such a rule runs, on the lamp the rule names
    c = ctx(lamps={"bottom": (False, None, None)})
    c.bind = ex.Bind(fridge="bottom")
    assert cmds(ex.fridge_start(c))[0].d == {"brightness_pct": 30, "color_temp_kelvin": 4500}
    # a fridge rule owns the trigger, so the hidden binding must not fire as well
    assert ex.fridge_rules_own_the_trigger(cfg["zones"][0]["detect_rules"], FRIDGE)
    assert not ex.fridge_rules_own_the_trigger([], FRIDGE)
    assert not ex.fridge_rules_own_the_trigger([room], FRIDGE)


def test_rules_can_be_added_and_deleted_and_an_empty_list_is_no_detection_control():
    cfg = two_rooms()
    assert Config(cfg).detect_rules("living_room") == []
    assert ex.plan_detect([], fridge_entity=FRIDGE, presence_entities=[ROOM]) == []
    rid = ops.add_detect_rule(cfg, "bedroom", sensor=ZONE_PRESENCE, lamps=["bed_ceiling", "bed_night"],
                              brightness="adaptive", off_delay_s=600)
    assert Config(normalise(cfg)).detect_rules("bedroom")[0]["id"] == rid
    ops.delete_detect_rule(cfg, "bedroom", rid)
    assert cfg["zones"][1]["detect_rules"] == []
    ops.add_detect_rule(cfg, "bedroom", sensor="binary_sensor.bedroom_presence", lamps=["bed_night"],
                        brightness="fixed", off_delay_s=10, brightness_pct=30, color_temp_kelvin=4500)
    ops.remove_lamp(cfg, "bed_night")
    assert cfg["zones"][1]["detect_rules"] == []          # the only lamp left, so the rule is gone
    assert validate(normalise(cfg)) == []


def test_off_choice_and_an_empty_field_both_clear_a_binding():
    assert ops.bindings_from_form({"top_group": "top", "fridge_lamp": "__off__", "green_lamp": ""}) == {"top_group": "top"}
    assert ops.bindings_from_form({}) == {}


def test_templates_are_optional_and_need_a_lamp_or_the_fridge_sensor():
    cfg = two_rooms()
    assert ops.room_template(cfg, "bedroom")["lamps"] == ["bed_ceiling", "bed_night"]   # first group, no top binding
    bare = {"version": 2, "params": {}, "sensors": [], "zones": [{"key": "hall", "name": "Hall", "params": {}, "bindings": {}}],
            "lamps": [], "groups": []}
    assert ops.room_template(bare, "hall") is None
    assert ops.fridge_template(bare, "hall") is None
    assert ops.room_template(plan_living_room(), "living_room")["sensor"] == ZONE_PRESENCE

"""0.4: one rule. The three examples differ by numbers, not by code.

Presence and the evening window gate the fridge example. min and max bound the action
and do not start it. The controller still only steps a lamp that is already on.
"""
from adaptive_light import core, flow_ops as ops
from adaptive_light.core import LampState
from adaptive_light.model import RULE_FIELDS, empty_config, example_rules, make_rule, normalise, validate


def _examples_config():
    cfg = empty_config()
    ops.add_zone(cfg, "Work station", "work_station")
    ops.add_zone(cfg, "Living room", "living_room")
    ops.add_lamps(cfg, "work_station", ["light.task_lamp"])
    ops.add_lamps(cfg, "living_room", ["light.fridge_lamp", "light.accent_lamp"])
    ops.add_group(cfg, "work_station", "Task", ["task_lamp"])
    ops.add_group(cfg, "living_room", "Fridge glow", ["fridge_lamp"])
    ops.add_group(cfg, "living_room", "Accent", ["accent_lamp"])
    cfg["rules"] = example_rules()
    return cfg


def test_the_three_examples_are_one_rule_with_different_numbers():
    rules = example_rules()
    assert len(rules) == 3
    assert [set(r) for r in rules] == [set(RULE_FIELDS)] * 3
    desk, fridge, accent = rules
    assert (desk["zone"], desk["group"], desk["lux_below"], desk["action"], desk["pct"], desk["max"]) == (
        "work_station", "task", 40, "target", 70, 80)
    assert (fridge["zone"], fridge["group"], fridge["lux_below"], fridge["action"], fridge["pct"], fridge["max"]) == (
        "living_room", "fridge_glow", 15, "step", 20, 40)
    assert fridge["presence"] is True and fridge["window"] == "evening"
    assert (accent["zone"], accent["group"], accent["lux_below"], accent["action"], accent["pct"], accent["evening_mult"]) == (
        "living_room", "accent", 30, "target", 25, 0.5)
    assert desk["presence"] is False and desk["window"] == "" and accent["presence"] is False
    # the same two functions read every example; nothing branches on the zone or the group
    assert validate(normalise(_examples_config())) == []
    stored = normalise(_examples_config())["rules"]
    assert [{k: r[k] for k in ("zone", "group", "lux_below", "action", "pct", "max", "evening_mult", "presence", "window")}
            for r in stored] == [
        {k: r[k] for k in ("zone", "group", "lux_below", "action", "pct", "max", "evening_mult", "presence", "window")}
        for r in rules]


def test_presence_and_the_evening_window_gate_the_fridge_example():
    desk, fridge, accent = example_rules()
    dark = dict(lux=10, presence=True, evening=True)
    assert core.rule_fires(fridge, **dark)
    assert not core.rule_fires(fridge, lux=10, presence=False, evening=True)
    assert not core.rule_fires(fridge, lux=10, presence=True, evening=False)
    assert not core.rule_fires(fridge, lux=15, presence=True, evening=True)   # below the threshold, not equal to it
    # the other two examples are the same function and do not ask for those gates
    assert core.rule_fires(desk, lux=30, presence=False, evening=False)
    assert core.rule_fires(accent, lux=20, presence=False, evening=False)
    assert not core.rule_fires(desk, lux=40, presence=True, evening=True)


def test_min_and_max_bound_the_action_and_do_not_trigger_it():
    desk, fridge, accent = example_rules()
    assert core.rule_brightness(desk, now_pct=10, evening=False) == 70
    louder = dict(desk)
    louder["pct"] = 90
    assert core.rule_brightness(louder, now_pct=10, evening=False) == 80          # max bounds a target
    assert not core.rule_fires(louder, lux=80, presence=True, evening=True)       # max does not open the rule
    assert core.rule_brightness(fridge, now_pct=10, evening=True) == 30           # 10 + 20, under the cap
    assert core.rule_brightness(fridge, now_pct=30, evening=True) == 40           # 30 + 20, capped
    assert core.rule_brightness(fridge, now_pct=90, evening=True) == 40           # already above max: still the cap, not a refusal
    assert core.rule_fires(fridge, lux=10, presence=True, evening=True)
    floored = make_rule(**{**fridge, "min": 35})
    assert core.rule_brightness(floored, now_pct=10, evening=True) == 35
    assert not core.rule_fires(floored, lux=20, presence=True, evening=True)      # min does not open it either
    assert core.rule_brightness(accent, now_pct=10, evening=False) == 25
    assert core.rule_brightness(accent, now_pct=10, evening=True) == round(25 * 0.5)


def test_the_controller_steps_only_a_lamp_that_is_already_on():
    desk = example_rules()[0]
    target = core.rule_brightness(desk, now_pct=10, evening=False)
    off = core.decide_lamp(
        "task", entity="light.task", lamp=LampState(False, None), target_pct=target,
        mode="Day", fridge_busy=False, switch_on=True, now_ts=0, pause_until_ts=0, modes=("Day",), fridge_block=False)
    assert off.act is False and off.entities == []
    on = core.decide_lamp(
        "task", entity="light.task", lamp=LampState(True, 26), target_pct=target,   # 26/255 ≈ 10%
        mode="Day", fridge_busy=False, switch_on=True, now_ts=0, pause_until_ts=0, modes=("Day",), fridge_block=False,
        step_pct=10, threshold_pct=3)
    assert on.act and on.new_pct == on.now_pct + 10 and on.new_pct <= desk["max"]

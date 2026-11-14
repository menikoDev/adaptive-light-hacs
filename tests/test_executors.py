"""Executors 5.2-5.12 as pure code: behaviour tests, plus parity with the plan's own templates where they compute values."""
import json, random, yaml, pytest

from adaptive_light import executors as ex
from adaptive_light.executors import Ctx, Cmd, Delay, Snapshot, Restore, MarkOwn, Log
from legacy import DEFAULT_LAMP_ENTITIES
from jinja_env import FakeHA, native

TOP = ("top_left", "top_right", "bottom")
DESK = ("desk", "stick")
ALL = TOP + DESK
ALL_ON = {k: True for k in ALL}
BIND = ex.Bind(top=TOP, desk=DESK, wake=("bottom", "top_left"), fridge="bottom", vent_left="top_right", vent_right="bottom", green="top_left")
LAMP_PARAMS = {"desk": {"work_hours_only": 0, "legacy_ratio": 1.0},
               "stick": {"work_hours_only": 1, "legacy_ratio": 0.25}}


def ctx(**kw):
    kw.setdefault("auto", dict(ALL_ON))
    kw.setdefault("bind", BIND)
    kw.setdefault("lamp_params", LAMP_PARAMS)
    return Ctx(**kw)


def cmds(steps): return [s for s in steps if isinstance(s, Cmd)]


# ---------------------------------------------------------------- gating

def test_managed_filters_orders_and_dedups():
    assert ex.managed(["a", "b", "a"], {"a": True, "b": True}) == ["a", "b"]
    assert ex.managed(TOP, {"top_left": True, "bottom": True}) == ["top_left", "bottom"]
    assert ex.managed(ALL, {}) == []


def test_every_executor_touches_nothing_when_all_switches_off():
    c = ctx(auto={k: False for k in ALL}, mode="Evening", lux=5, hour=10, scene="Work",
            lamps={k: (True, 200, 4500) for k in ALL})
    plans = [
        ex.movie_start(c), ex.movie_end(c, "Day"), ex.vent_blink(c, trigger="advice_changed", advice="open_both"),
        ex.evening_on(c), ex.desk_on(c), ex.scene_apply(c), ex.fridge_start(c), ex.fridge_end(c, {"was_on": True, "white": False}),
        ex.night_off(c), ex.desk_off(c), ex.top_off(c), ex.green_fill(c, trigger="dark", at_desk=True),
        ex.night_wake(ctx(auto={k: False for k in ALL}, mode="Night"), trigger="motion", saved_color="k:2700"),
    ]
    for p in plans:
        assert not [s for s in p if isinstance(s, Cmd) and s.keys and s.action == "on"], p
        assert not [s for s in p if isinstance(s, (Snapshot,)) and s.keys], p
    # fridge_end with the switch off must not touch the lamp either (re-check at the end, plan 5.7)
    assert not cmds(ex.fridge_end(c, {"was_on": False, "white": False}))


def test_a_single_lamp_off_is_skipped_only_for_that_lamp():
    auto = dict(ALL_ON, top_right=False)
    c = ctx(auto=auto)
    assert [x.keys for x in cmds(ex.top_off(c))] == [("top_left", "bottom")]
    c2 = ctx(auto=auto, mode="Evening", hour=22, targets={"top_left": 11, "top_right": 12, "bottom": 7}, enabled=True, apply=True)
    on_keys = [x.keys for x in cmds(ex.evening_on(c2))]
    assert on_keys == [("top_left",), ("bottom",)]


# ---------------------------------------------------------------- 5.2 movie

def test_movie_start_and_end():
    c = ctx(lux=10, now_ts=100.0, last_set_ts=98.0, lamps={"top_left": (True, 100, None)})
    s = ex.movie_start(c)
    assert s[0] == Delay(4) and s[1] == Snapshot("movie_before", TOP)
    assert cmds(s)[0].d == {"brightness_pct": 8, "transition": 3}
    # bright room and every lamp off: snapshot only, no dimming
    s = ex.movie_start(ctx(lux=80, lamps={}))
    assert not cmds(s) and any(isinstance(x, Snapshot) for x in s)
    assert ex.movie_end(ctx(lux=60), "Night") == []
    assert cmds(ex.movie_end(ctx(lux=60), "Day"))[0].action == "off"
    assert ex.movie_end(ctx(lux=10), "Evening")[-1] == Restore("movie_before", 3)
    assert ex.fade_wait(100, 0) == 0 and ex.fade_wait(100, 99.2) == 6


# ---------------------------------------------------------------- 5.3 vent

def test_vent_conditions_and_patterns():
    base = dict(vent_mode=True, mode="Day", presence=True, fridge_busy=False, c=ctx(), trigger="advice_changed")
    assert ex.vent_allowed(**base)
    for bad in (dict(vent_mode=False), dict(mode="Movie"), dict(presence=False), dict(fridge_busy=True)):
        assert not ex.vent_allowed(**{**base, **bad})
    assert not ex.vent_allowed(**{**base, "trigger": "left_moved", "from_value": 10, "to_value": 12})
    assert ex.vent_allowed(**{**base, "trigger": "left_moved", "from_value": 10, "to_value": 20})
    assert not ex.vent_allowed(**{**base, "c": ctx(auto={k: k not in ("top_right", "bottom") for k in ALL})})
    c = ctx(lamps={"top_right": (True, 255, None), "bottom": (True, 10, None)})
    adv = ex.vent_blink(c, trigger="advice_changed", advice="open_left")
    ons = [s for s in cmds(adv) if s.action == "on"]
    assert ons[0].d["color_name"] == "blue" and ons[0].d["brightness_pct"] == 60      # clamped 100 -> 60
    assert [s for s in ons if s.d["color_name"] == "blue" and s.keys == ("bottom",)][0].d["brightness_pct"] == 20  # 4 -> 20
    assert ons[-1].d["color_name"] == "green" and ons[-1].keys == ("top_right",)
    wrong = ex.vent_blink(c, trigger="left_moved", advice="open_right")
    assert sum(1 for s in cmds(wrong) if s.d.get("color_name") == "red") == 3
    assert ex.vent_restore_needed("Day", "Day") and not ex.vent_restore_needed("Movie", "Day")


# ---------------------------------------------------------------- 5.4-5.6

def test_evening_shadow_vs_apply():
    t = {"top_left": 11, "top_right": 12, "bottom": 7}
    sh = ex.evening_on(ctx(hour=22, targets=t))
    assert [s.d["brightness_pct"] for s in cmds(sh)] == [30, 30, 30] and isinstance(sh[-1], Log) and "adaptive would be top_left 11%" in sh[-1].message
    ap = ex.evening_on(ctx(hour=22, targets=t, enabled=True, apply=True))
    assert [s.d["brightness_pct"] for s in cmds(ap)] == [11, 12, 7] and not isinstance(ap[-1], Log)
    assert cmds(ap)[0].d["color_temp_kelvin"] == 2700
    assert [s.d["brightness_pct"] for s in cmds(ex.evening_on(ctx(hour=10, targets={})))] == [60, 60, 60]
    assert ex.evening_allowed(ctx(mode="Evening", lux=39), presence=True, group_off=True)
    assert not ex.evening_allowed(ctx(mode="Evening", lux=40), presence=True, group_off=True)


def test_desk_on_work_hours_and_gates():
    c = ctx(hour=16, scene="Bright", lux=100, targets={"desk": 70, "stick": 15}, enabled=True, apply=True)
    assert [x.keys for x in cmds(ex.desk_on(c))] == [("desk",)]          # stick only 06:00-15:00
    c = ctx(hour=9, scene="Bright", lux=100, targets={"desk": 70, "stick": 15}, enabled=True, apply=True)
    assert [(x.keys, x.d["brightness_pct"]) for x in cmds(ex.desk_on(c))] == [(("desk",), 70), (("stick",), 15)]
    assert not ex.desk_on_allowed(ctx(mode="Night"), at_table=True) and ex.desk_on_allowed(ctx(mode="Movie"), at_table=True)
    s = ex.scene_apply(ctx(scene="Soft", lux=0, lamps={"desk": (True, 100, None)}))
    assert [x.keys for x in cmds(s)] == [("desk",)] and isinstance(s[-1], Log)   # off stick is not switched on
    assert ex.scene_apply(ctx(mode="Night")) == []


# ---------------------------------------------------------------- 5.7 fridge

def test_fridge_flow():
    c = ctx(lamps={"top_left": (True, 100, 2700), "bottom": (False, None, None)}, now_ts=50, last_set_ts=49)
    s = ex.fridge_start(c)
    assert s[0] == Delay(5) and cmds(s)[0].d == {"brightness_pct": 30, "color_temp_kelvin": 4500}
    f0 = ex.fridge_context(c)
    assert f0 == {"white": False, "was_on": True}
    assert ex.fridge_end(c, f0)[-1] == Restore("fridge_before", 3)
    f1 = ex.fridge_context(ctx(lamps={"bottom": (True, 80, 4500)}))
    assert f1 == {"white": True, "was_on": False}
    assert cmds(ex.fridge_end(c, f1))[0].action == "off"
    assert not ex.fridge_start(ctx(mode="Away")) and not ex.fridge_start(ctx(auto={"bottom": False}))


# ---------------------------------------------------------------- 5.8-5.12

def test_off_automations_conditions():
    kw = dict(tv_on=False, presence=False, table=False, carpet=False, entrance=False, fridge=False)
    c = ctx(mode="Night")
    assert ex.night_off_allowed(c, trigger="night_start", **kw)
    assert not ex.night_off_allowed(c, trigger="night_start", **{**kw, "presence": True})
    assert not ex.night_off_allowed(c, trigger="night_start", **{**kw, "tv_on": True})
    assert ex.night_off_allowed(c, trigger="sofa_asleep", **{**kw, "presence": True})
    assert not ex.night_off_allowed(c, trigger="sofa_asleep", **{**kw, "carpet": True})
    assert not ex.night_off_allowed(ctx(mode="Day"), trigger="night_start", **kw)
    assert not ex.desk_off_allowed(ctx(mode="Night")) and ex.desk_off_allowed(ctx(mode="Day"))
    assert ex.top_off_allowed(ctx(mode="Away"), tv_on=True, presence=True)
    assert ex.top_off_allowed(ctx(mode="Day"), tv_on=False, presence=False)
    assert not ex.top_off_allowed(ctx(mode="Day"), tv_on=True, presence=False)
    assert not ex.top_off_allowed(ctx(mode="Night"), tv_on=False, presence=False)
    assert cmds(ex.night_off(c))[0].keys == TOP + DESK
    assert cmds(ex.desk_off(c))[0].d == {"transition": 3}


def test_green_fill_branches():
    c = ctx(mode="Day", hour=10, lux=20)
    assert cmds(ex.green_fill(c, trigger="dark", at_desk=True))[0].d["hs_color"] == (120, 73)
    assert ex.green_fill(c, trigger="dark", at_desk=False) == []
    assert cmds(ex.green_fill(c, trigger="leave", at_desk=False))[0].action == "off"
    assert cmds(ex.green_fill(ctx(mode="Day", hour=16, lux=20), trigger="dark", at_desk=True))[0].action == "off"
    assert cmds(ex.green_fill(ctx(mode="Day", hour=10, lux=90), trigger="bright", at_desk=True))[0].action == "off"
    assert ex.green_fill(ctx(mode="Night"), trigger="dark", at_desk=True) == []
    assert ex.green_fill(ctx(auto={"top_left": False}), trigger="dark", at_desk=True) == []


def test_night_wake():
    c = ctx(mode="Night", lamps={})
    assert cmds(ex.night_wake(c, trigger="motion", saved_color="hs:30,40"))[0].d == {"brightness_pct": 5, "hs_color": (30.0, 40.0), "transition": 2}
    assert cmds(ex.night_wake(c, trigger="motion", saved_color="junk"))[0].d["color_temp_kelvin"] == 2700
    assert ex.night_wake(ctx(mode="Night", lamps={"bottom": (True, 10, None)}), trigger="motion", saved_color="k:3000") == []
    assert cmds(ex.night_wake(c, trigger="quiet", saved_color=""))[0].keys == ("bottom", "top_left")
    only = ctx(mode="Night", auto={"bottom": False, "top_left": False})
    assert ex.night_wake(only, trigger="motion", saved_color="k:3000") == [] and ex.night_wake(only, trigger="quiet", saved_color="") == []


# ---------------------------------------------------------------- parity with the plan's own templates

@pytest.fixture(scope="module")
def blocks(plan_blocks):
    macros = next(b for l, b in plan_blocks if l == "jinja" and "macro managed" in b)
    autos = {}
    for l, b in plan_blocks:
        if l == "yaml" and b.lstrip().startswith(("alias:",)):
            d = yaml.safe_load(b); autos[d["alias"]] = d
    return macros, autos


def test_managed_macro_parity(blocks):
    macros, _ = blocks
    ha = FakeHA(); rng = random.Random(1)
    lamp_ent = DEFAULT_LAMP_ENTITIES
    queries = [(["light.living_room_top_lamp_group"], TOP), (["light.living_room_light_group"], TOP),
               (["light.living_room_light_group", "light.work_station_lamp", "light.work_station_light_stick"], TOP + DESK),
               (["light.work_station_lamp", "light.work_station_light_stick"], DESK),
               (["light.living_room_top_lamp_bottom", "light.living_room_top_lamp_top_left"], ("bottom", "top_left"))]
    for _ in range(200):
        auto = {k: rng.random() < .5 for k in ALL}
        for k, v in auto.items(): ha.put(f"input_boolean.light_{k}_auto", "on" if v else "off")
        for ents, keys in queries:
            got = json.loads(ha.render("{% from 'adaptive_light.jinja' import managed %}{{ managed(ents) }}", macros, ents=ents))
            assert got == [lamp_ent[k] for k in ex.managed(keys, auto)]


def test_desk_legacy_formula_parity(blocks):
    _, autos = blocks
    v = autos["Desk: lights on at table"]["actions"][0]["variables"]
    ha = FakeHA(); rng = random.Random(2)
    for _ in range(300):
        scene = rng.choice(["Work", "Bright", "Soft", "Odd"]); lux = rng.choice([0, 3, 10, 55, 100, 400])
        ha.put("input_select.work_station_desk_light_scene", scene)
        ha.put("sensor.living_room_presence_sensor_light_sensor_light_level", lux)
        ha.put("input_boolean.light_adaptive_enabled", "on"); ha.put("input_boolean.light_adaptive_apply", "off")
        ctx_ = {}
        for name in ("scene_pct", "lux", "factor", "legacy_lamp", "legacy_stick"):
            ctx_[name] = native(ha.render(v[name], **ctx_))
        assert (ex.desk_legacy(scene, lux), ex.desk_legacy(scene, lux, 0.25)) == (ctx_["legacy_lamp"], ctx_["legacy_stick"])


def test_saved_color_parity(blocks):
    _, autos = blocks
    v = autos["Light: night wake dim light"]["actions"][1]["default"][1]["variables"]
    ha = FakeHA()
    for saved in ["hs:30,40", "hs:12.5,80", "k:3000", "k:abc", "hs:1", "", "hs:a,b", "weird", "k:2200"]:
        ha.put("input_text.living_room_top_lamps_color", saved)
        ctx_ = {}
        for name in ("saved", "is_hs", "is_k", "hs", "kelvin"):
            ctx_[name] = native(ha.render(v[name], **ctx_)) if name != "saved" else ha.render(v[name], **ctx_)
        kind, val = ex.parse_saved_color(saved)
        if ctx_["is_hs"] and len(ctx_["hs"]) == 2:
            assert kind == "hs" and list(val) == [float(x) for x in ctx_["hs"]]
        else:
            assert kind == "k" and val == ctx_["kelvin"]

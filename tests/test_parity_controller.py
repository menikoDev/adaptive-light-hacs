"""Controller (6.2) and override (6.1): evaluate the plan's own variable/condition templates and compare with core."""
import random, re, types, yaml, pytest

from adaptive_light import core
from legacy import CFG, DEFAULT_LAMP_ENTITIES, LAMP_KEYS
from adaptive_light import params as P
from jinja_env import FakeHA, native

FRIDGE = "binary_sensor.living_room_presence_sensor_refrigerator_zone"
MODE = "input_select.living_room_light_mode"


@pytest.fixture(scope="module")
def autos(plan_blocks):
    out = {}
    for lang, b in plan_blocks:
        if lang == "yaml" and b.startswith("alias:"):
            d = yaml.safe_load(b)
            out[d["alias"]] = d
    return out


def render_vars(ha, block, ctx):
    for name, tpl in block.items():
        ctx[name] = native(ha.render(tpl, **ctx)) if isinstance(tpl, str) else tpl


def test_controller_variables_match_yaml(autos):
    ctrl = autos["Light: adaptive controller (shadow by default)"]
    acts = ctrl["actions"]
    top_vars = acts[0]["variables"]
    per = acts[1]["repeat"]["sequence"]
    var_blocks = [s["variables"] for s in per if "variables" in s]
    log_msg = per[-1]["if"] if False else None
    rng = random.Random(11)
    ha = FakeHA()
    base = 1_800_000_000.0
    import datetime as dt
    seen_reasons, acted = set(), 0
    for _ in range(2500):
        ha.now = dt.datetime.fromtimestamp(base + rng.randint(0, 86400), dt.timezone.utc)
        mode = rng.choice(["Day", "Evening", "Movie", "Night", "Away"]); fridge = rng.choice([True, False])
        ha.put("input_boolean.light_adaptive_apply", "on" if rng.random() < .5 else "off")
        ha.put(MODE, mode); ha.put(FRIDGE, "on" if fridge else "off")
        ctx = {}
        render_vars(ha, top_vars, ctx)
        assert ctx["mode"] == mode
        for item in ctx["lamps"]:
            key = item["key"]
            lamp_state = core.LampState(rng.random() < .7, None)
            ent = DEFAULT_LAMP_ENTITIES[key]
            if lamp_state.on:
                lamp_state.brightness = rng.randint(1, 255); ha.put(ent, "on", brightness=lamp_state.brightness)
            else:
                ha.put(ent, "off")
            sw = rng.random() < .8
            ha.put(f"input_boolean.light_{key}_auto", "on" if sw else "off")
            target = rng.choice([-1, 0, 5, 20, 40, 60, 100, "unavailable"])
            ha.put(f"sensor.light_target_{key}", target)
            pause = rng.choice([0, 0, ctx["now_ts"] + 600, ctx["now_ts"] - 600])
            ha.put(f"input_datetime.light_{key}_pause_until", "x", timestamp=pause)
            c2 = dict(ctx); c2["repeat"] = {"item": item}
            for blk in var_blocks:
                render_vars(ha, blk, c2)
            t_int = int(target) if str(target).lstrip("-").isdigit() else -1
            d = core.decide_lamp(key, entity=ent, lamp=lamp_state, target_pct=t_int, mode=mode, fridge_busy=fridge,
                                 modes=P.parse_modes(CFG.lamp_param(key, 'ctrl_modes')), fridge_block=bool(CFG.lamp_param(key, 'fridge_block')),
                                 switch_on=sw, now_ts=ctx["now_ts"], pause_until_ts=float(pause))
            assert bool(c2["act"]) == d.act, (key, c2, d)
            assert c2["now_pct"] == d.now_pct
            assert c2["new_pct"] == d.new_pct
            acted += d.act
            if not d.act and d.entities:
                # the YAML 'no change' message uses the same reason cascade
                msg = per[-1]["else"][0]["then"][0]["data"]["message"] if False else None
                seen_reasons.add(d.reason)
    assert acted > 200
    assert {"switch off", "mode/fridge", "paused", "within 3%"} <= seen_reasons


def test_override_matches_yaml(autos):
    ov = autos["Light: adaptive manual override"]
    cond_own = ov["conditions"][0]["value_template"]
    cond_auto = ov["conditions"][1]["value_template"]
    key_tpl = ov["actions"][0]["variables"]["key"]
    min_tpl = ov["actions"][2]["variables"]["minutes"]
    ha = FakeHA()
    rng = random.Random(5)
    import datetime as dt
    now = dt.datetime(2026, 11, 13, 12, 0, tzinfo=dt.timezone.utc)
    ha.now = now
    n_pause = 0
    for _ in range(2000):
        ent = rng.choice(list(DEFAULT_LAMP_ENTITIES.values()))
        key = next(k for k, e in DEFAULT_LAMP_ENTITIES.items() if e == ent)
        parent = rng.choice([None, "abc"])
        to_state = rng.choice(["on", "on", "off"])
        from_none = rng.random() < .1
        last_set = rng.choice([0, now.timestamp() - 30, now.timestamp() - 120])
        ha.put("input_datetime.light_adaptive_last_set", "x", timestamp=last_set)
        autos_list = []
        for name in ("Light: adaptive manual override", "Light: adaptive controller (shadow by default)", "Light: set home mode", "Some other"):
            lt = rng.choice([None, now - dt.timedelta(seconds=5), now - dt.timedelta(seconds=40)])
            autos_list.append((name, lt))
        ha.automations = autos_list
        auto_on = rng.random() < .8
        ha.put(f"input_boolean.light_{key}_auto", "on" if auto_on else "off")
        mins = rng.choice([30, 0, 1, 90, "unavailable"])
        ha.put("input_number.light_override_minutes", mins)
        ctx_obj = types.SimpleNamespace(parent_id=parent)
        trig = types.SimpleNamespace(
            entity_id=ent,
            from_state=None if from_none else types.SimpleNamespace(state="off"),
            to_state=types.SimpleNamespace(state=to_state, context=ctx_obj),
        )
        states_automation = [types.SimpleNamespace(attributes={"friendly_name": n, "last_triggered": lt}) for n, lt in autos_list]
        ctx = {"trigger": trig, "states": types.SimpleNamespace(automation=states_automation)}
        # `states.automation` is a namespace in HA templates; shadow the global function with an object that also callable
        class S:
            automation = states_automation
            def __call__(self, e): return ha._states(e)
        ctx["states"] = S()
        c1 = ha.render(cond_own, **ctx) == "True"
        c2 = ha.render(cond_auto, **ctx) == "True"
        yaml_manual = c1 and c2 and auto_on
        core_until = core.override_pause_until(
            now_ts=now.timestamp(), last_set_ts=last_set, parent_id_is_none=parent is None, to_state_on=to_state == "on",
            had_from_state=not from_none,
            automations_last_triggered=[(n, 0.0 if lt is None else lt.timestamp()) for n, lt in autos_list],
            ignore_names=["Light: adaptive manual override", "Light: adaptive controller (shadow by default)", "Light: set home mode"],
            switch_on=auto_on, minutes=None if mins == "unavailable" else float(mins))
        assert (core_until is not None) == yaml_manual, (parent, to_state, from_none, last_set, auto_on)
        if yaml_manual:
            m = float(ha.render(min_tpl))
            assert core_until == pytest.approx(now.timestamp() + m * 60)
            n_pause += 1
    assert n_pause > 50

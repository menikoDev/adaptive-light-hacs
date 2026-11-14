"""Run the plan's OWN Jinja macros / templates (extracted from docs/adaptive-light-plan.md at test time)
against the Python core on thousands of random scenarios and require identical results.

This proves formula parity with the YAML package. It does not prove Home Assistant integration
behaviour (see test_ha_stand.py / README for that)."""
import random, yaml, pytest

from adaptive_light import core
from legacy import DEFAULTS, LAMP_KEYS, DEFAULT_LAMP_ENTITIES
from jinja_env import FakeHA, native

LUX = "sensor.living_room_presence_sensor_light_sensor_light_level"
RAD = "sensor.general_graz_meteo_station_global_radiation"
ELEV = "sensor.general_sun_solar_elevation"
MODE = "input_select.living_room_light_mode"
SCENE = "input_select.work_station_desk_light_scene"


@pytest.fixture(scope="module")
def plan(plan_blocks):
    macros = next(b for lang, b in plan_blocks if lang == "jinja" and "macro need" in b)
    pkg = next(yaml.safe_load(b) for lang, b in plan_blocks if lang == "yaml" and "light_adaptive_enabled:" in b and "daylight_factor" in b and b.startswith("input_boolean:"))
    opn = next(yaml.safe_load(b) for lang, b in plan_blocks if lang == "yaml" and b.startswith("input_number:") and "light_openness_raw" in b)
    tmpl = {s["unique_id"]: s for t in pkg["template"] if "sensor" in t for s in t["sensor"]}
    tmpl.update({s["unique_id"]: s for t in opn["template"] if "sensor" in t for s in t["sensor"]})
    smooth = next(t for t in opn["template"] if "triggers" in t)
    return macros, tmpl, smooth


def scenario(rng):
    p = dict(DEFAULTS)
    for k in LAMP_KEYS:
        p[f"light_k_{k}"] = round(rng.uniform(0.05, 1.2), 3)
    p["light_dark_lux_offset"] = rng.choice([0, 0, 0.5, 2.0])
    if rng.random() < 0.3:
        k = rng.choice(LAMP_KEYS); p[f"light_{k}_w_out"] = rng.choice([0, 1, 0.3]); p[f"light_{k}_w_in"] = rng.choice([0, 1, 0.7])
    p["light_blind_lux_floor"] = rng.choice([0, 15, 40])
    p["light_open_k"] = rng.choice([0.5, 1.0, 3.4])
    p["light_gain"] = 1
    for k in LAMP_KEYS:
        p[f"light_{k}_gain"] = rng.choice([1.0, 1.0, 0.5, 2.0])
    lamps = {}
    all_off = rng.random() < 0.4
    for k in LAMP_KEYS:
        if not all_off and rng.random() < 0.5:
            lamps[k] = core.LampState(True, rng.randint(1, 255))
        else:
            lamps[k] = core.LampState(False, None)
    rad = rng.choice([None, 0, 30, 75, 150, 400, 900, "unavailable"])
    elev = rng.choice([None, -30, -8, 0, 15, 40, "unavailable"])
    return dict(
        p=p, lamps=lamps, lux=rng.choice([0, 1, 5, 20, 80, 200, 600, None]), rad=rad, elev=elev,
        mode=rng.choice(["Day", "Evening", "Movie", "Night", "Away"]), scene=rng.choice(["Work", "Bright", "Soft", "Weird"]),
        hour=rng.randint(0, 23), openness=rng.choice([100, 100, 80, 45, 12.5, 0]),
    )


def load(ha, sc):
    for pid, v in sc["p"].items():
        ha.put(f"input_number.{pid}", v)
    for k, st in sc["lamps"].items():
        ent = DEFAULT_LAMP_ENTITIES[k]
        if st.on: ha.put(ent, "on", brightness=st.brightness)
        else: ha.put(ent, "off")
    ha.put(LUX, sc["lux"] if sc["lux"] is not None else "unavailable")
    ha.put(RAD, sc["rad"] if sc["rad"] is not None else "unavailable")
    ha.put(ELEV, sc["elev"] if sc["elev"] is not None else "unavailable")
    ha.put(MODE, sc["mode"]); ha.put(SCENE, sc["scene"])
    ha.put("sensor.light_openness", sc["openness"])
    ha.put("input_number.light_evening_start_hour", sc["p"]["light_evening_start_hour"])
    for n in ("work", "bright", "soft"):
        ha.put(f"input_number.light_desk_scene_{n}", sc["p"][f"light_scene_{n}"])


def core_inputs(sc, daylight):
    ev = core.evening_quiet(sc["hour"], sc["p"])
    num = lambda v: None if v in (None, "unavailable") else float(v)
    return core.Inputs(lux=num(sc["lux"]), daylight=daylight, openness=sc["openness"], mode=sc["mode"], scene=sc["scene"],
                       evening_quiet=ev, lamps=sc["lamps"], params=sc["p"])


def test_formulas_match_yaml(plan):
    macros, tmpl, _ = plan
    rng = random.Random(20261113)
    ha = FakeHA()
    n = 0
    for _ in range(1500):
        sc = scenario(rng)
        load(ha, sc)
        # derived sensors the macros read
        dl_yaml = ha.render(tmpl["daylight_factor"]["state"])
        ha.put("sensor.daylight_factor", dl_yaml if dl_yaml.lstrip("-").isdigit() else "unavailable")
        avail = ha.render(tmpl["daylight_factor"]["availability"]) == "True"
        num = lambda v: None if v in (None, "unavailable") else float(v)
        dl_core = core.daylight_factor(num(sc["rad"]), num(sc["elev"]))
        assert (dl_core is not None) == avail
        if dl_core is not None:
            assert int(dl_yaml) == dl_core, sc
        ha.put("binary_sensor.light_evening_quiet", "on" if core.evening_quiet(sc["hour"], sc["p"]) else "off")
        i = core_inputs(sc, dl_core)
        # ambient
        a_yaml = ha.render(tmpl["living_room_ambient_lux"]["state"], macros)
        if sc["lux"] is not None:
            assert float(a_yaml) == pytest.approx(core.ambient(i.lux, i.lamps, i.params)), sc
        # trust / need / targets only matter when their availability is true
        if sc["lux"] is not None and dl_core is not None:
            assert int(ha.render(tmpl["light_ambient_trust"]["state"], macros)) == core.trust(i.daylight, i.openness, i.lux, i.lamps, i.params), sc
            assert int(ha.render(tmpl["light_need"]["state"], macros)) == round(core.need("", i)), sc
            for k in LAMP_KEYS:
                got = int(ha.render(tmpl[f"light_target_{k}"]["state"], macros))
                assert got == core.target(k, i), (k, sc)
            n += 1
    assert n > 500


def test_openness_raw_and_smoothing_match_yaml(plan):
    macros, tmpl, smooth = plan
    rng = random.Random(7)
    ha = FakeHA()
    raw_t = tmpl["light_openness_raw"]
    checked = 0
    for _ in range(1500):
        sc = scenario(rng)
        load(ha, sc)
        dl_y = ha.render(tmpl["daylight_factor"]["state"])
        ha.put("sensor.daylight_factor", dl_y if dl_y.lstrip("-").isdigit() else "unavailable")
        num = lambda v: None if v in (None, "unavailable") else float(v)
        dl = core.daylight_factor(num(sc["rad"]), num(sc["elev"]))
        avail = ha.render(raw_t["availability"], macros) == "True"
        raw = core.openness_raw(num(sc["lux"]), dl, sc["mode"], sc["lamps"], sc["p"])
        assert avail == (raw is not None), (avail, raw, dl, sc["mode"], sc["lux"], sc["p"]["light_open_k"], sc["p"]["light_open_min_daylight"], sc["rad"], sc["elev"])
        if avail:
            assert int(ha.render(raw_t["state"], macros)) == raw
            checked += 1
    assert checked > 100

    # smoothing: evaluate the YAML 'variables' chain with a random walk of raw values
    ha = FakeHA(); params = dict(DEFAULTS)
    for pid, v in params.items(): ha.put(f"input_number.{pid}", v)
    st_c = core.OpennessState()
    ha.put("sensor.light_openness", 100); ha.a["sensor.light_openness"] = {"low_count": 0}
    seq = [100] * 3 + [30] * 12 + [95] * 8 + [None] * 3 + [60] * 4 + [20] * 7 + [100] * 6
    rng = random.Random(3)
    seq += [rng.choice([None, rng.randint(0, 100)]) for _ in range(300)]
    for raw in seq:
        ha.put("sensor.light_openness_raw", "unavailable" if raw is None else raw)
        ctx = {"trigger": type("T", (), {"id": "tick"})()}
        for name, tpl in smooth["variables"].items():
            ctx[name] = native(ha.render(tpl, **ctx)) if name not in ("raw_s",) else ha.render(tpl, **ctx)
        new = float(ha.render(smooth["sensor"][0]["state"], **ctx))
        lc = int(ha.render(smooth["sensor"][0]["attributes"]["low_count"], **ctx))
        st_c = core.openness_step(st_c, raw, params)
        assert new == pytest.approx(st_c.value), (raw, new, st_c)
        assert lc == st_c.low_count
        ha.put("sensor.light_openness", new); ha.a["sensor.light_openness"] = {"low_count": lc}

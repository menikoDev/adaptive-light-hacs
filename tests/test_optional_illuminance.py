"""A zone without an illuminance sensor (0.3.0): daylight-only estimate for the formulas, executors and the controller,
no openness/trust."""
from adaptive_light import core, executors as ex, flow_ops as ops
from adaptive_light.model import validate
from test_manager_zones import St, make, run
from helpers_cfg import two_rooms

BED_LUX = "sensor.bedroom_lux"


def make_bare(**kw):
    """Same as make(), but the bedroom has no illuminance sensor."""
    m, hass = make(**kw)
    raw = m.cfg.to_dict()
    raw["sensors"] = [s for s in raw["sensors"] if not (s["zone"] == "bedroom" and s["role"] == "illuminance")]
    m.set_config(raw)
    return m, hass


def test_config_without_illuminance_is_valid():
    cfg = two_rooms()
    ops.set_sensor(cfg, "illuminance", None, "bedroom")
    assert validate(cfg) == []


def test_estimate_lux_is_daylight_through_windows_plus_lamps():
    p = {"light_open_k": 0.8, "light_dark_lux_offset": 1.0, "light_k_a": 0.5}
    lamps = {"a": core.LampState(True, 255)}
    assert core.estimate_lux(None, lamps, p) is None
    assert core.estimate_lux(50, {}, p) == 41.0                         # 0.8 * 50 + 1
    est = core.estimate_lux(50, lamps, p)
    assert est == 91.0                                                   # lamp at 100 % adds k * 100
    assert core.ambient(est, lamps, p) == 40.0                           # the lamps cancel out: ambient = open_k * daylight


def test_zone_without_sensor_uses_daylight_estimate_and_ignores_its_lamps():
    m, hass = make_bare(rad=75, lux2=999)                                # radiation 75 -> daylight 50; the stale lux entity is ignored
    m.recompute()
    z = m.values["zones"]["bedroom"]
    assert z["lux_source"] == "daylight_estimate" and m.values["zones"]["living_room"]["lux_source"] == "sensor"
    assert z["ambient"] == 50.0 and z["need"] is not None
    assert z["trust"] is None and z["openness"] is None and z["openness_raw"] is None
    before = z["ambient"]
    hass.states["light.bed_ceiling"] = St("on", brightness=255)
    m.recompute()
    assert m.values["zones"]["bedroom"]["ambient"] == before             # no feedback from the lamps
    assert m.values["lamps"]["bed_ceiling"]["target"] is not None


def test_need_follows_daylight_only():
    day, _ = make_bare(rad=300); night, _ = make_bare(rad=0)
    day.recompute(); night.recompute()
    assert night.values["zones"]["bedroom"]["need"] > day.values["zones"]["bedroom"]["need"]
    assert night.values["zones"]["bedroom"]["need"] == 100                # no light at all: full need
    assert day.values["zones"]["bedroom"]["ambient"] == 100.0             # open_k 1.0 * daylight 100


def test_no_daylight_and_no_sensor_means_unavailable_not_a_guess():
    m, hass = make_bare()
    for e in ("sensor.general_graz_meteo_station_global_radiation", "sensor.general_sun_solar_elevation"):
        hass.states[e] = St("unavailable")
    m.recompute()
    z = m.values["zones"]["bedroom"]
    assert z["ambient"] is None and z["need"] is None and m.values["lamps"]["bed_ceiling"]["target"] is None


def test_controller_still_steps_lamps_of_a_zone_without_sensor():
    m, hass = make_bare(mode="Evening", rad=0, lux=5)
    hass.states["light.bed_ceiling"] = St("on", brightness=int(0.5 * 255))
    run(m.controller_tick())
    calls = [c[2] for c in hass.services.calls if c[:2] == ("light", "turn_on")]
    assert [c["entity_id"] for c in calls] == [["light.bed_ceiling"]] and calls[0]["brightness_pct"] == 60   # 50 -> 60, target 100


def test_openness_of_a_zone_without_sensor_never_moves_and_does_not_distort_need():
    m, _ = make_bare(rad=900)
    m.openness["bedroom"] = type(m.openness["bedroom"])(10.0, 0)         # stale value from the time a sensor existed
    m.recompute()
    assert m.inputs_for("bedroom").openness == 100.0
    for _ in range(6):
        m.openness_tick()
    assert m.openness["bedroom"].value == 10.0                           # frozen, not used


def test_executors_get_the_estimated_lux():
    m, _ = make_bare(rad=300, mode="Evening")                            # daylight 100 -> estimated 100 lx
    assert m.exec_ctx("bedroom").lux == 100.0
    m2, _ = make_bare(rad=0, mode="Evening")
    assert m2.exec_ctx("bedroom").lux == 0.0
    c2, c1 = m2.exec_ctx("bedroom"), m.exec_ctx("bedroom")
    c2.mode = c1.mode = "Evening"          # the mode entity belongs to the living room zone in this fixture
    assert ex.evening_allowed(c2, presence=True, group_off=True)        # dark by estimate: evening light may start
    assert not ex.evening_allowed(c1, presence=True, group_off=True)    # bright by estimate: it may not
    assert m.exec_ctx("living_room").lux == 20.0                         # zones with a sensor keep the measured value

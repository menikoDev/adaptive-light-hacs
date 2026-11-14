"""Manager and services with a fake hass: multi-zone pipeline, persistence across a 'restart', group pause, calibration."""
import asyncio, datetime as dt, types
import pytest

from adaptive_light import calibration, flow_ops as ops, services as svc
from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.persistence import MemoryStore
from legacy import DEFAULT_LAMP_ENTITIES
from helpers_cfg import two_rooms

LUX1 = "sensor.living_room_presence_sensor_light_sensor_light_level"
NOW = dt.datetime(2026, 11, 13, 12, 3, tzinfo=dt.timezone.utc)


class St:
    def __init__(self, state, ctx=None, **attrs):
        self.state, self.attributes = state, attrs
        self.context = ctx or types.SimpleNamespace(id="u", parent_id=None)


class FakeStates(dict):
    def async_all(self, domain): return []


class FakeServices:
    def __init__(self, states=None): self.calls, self.states = [], states
    async def async_call(self, domain, service, data, blocking=False, context=None):
        self.calls.append((domain, service, data))


def make(mode="Day", lux=20, lux2=15, rad=600, apply=True, store=None, persisted=None, now=NOW):
    s = FakeStates()
    hass = types.SimpleNamespace(states=s, services=FakeServices(), async_create_task=lambda c: asyncio.ensure_future(c))
    saved = []
    m = AdaptiveLightManager(hass, two_rooms(), now=lambda: now, store=store, persist_config=saved.append)
    m.saved_configs = saved
    s[LUX1] = St(str(lux)); s["sensor.bedroom_lux"] = St(str(lux2))
    s["binary_sensor.bedroom_presence"] = St("off")
    s["sensor.general_graz_meteo_station_global_radiation"] = St(str(rad))
    s["sensor.general_sun_solar_elevation"] = St("30")
    s["input_select.living_room_light_mode"] = St(mode)
    s["input_select.work_station_desk_light_scene"] = St("Work")
    for e in list(DEFAULT_LAMP_ENTITIES.values()) + ["light.bed_ceiling", "light.bed_night"]:
        s[e] = St("off")
    m.state.enabled, m.state.apply = True, apply
    for k in m.cfg.lamps: m.state.auto[k] = True
    return m, hass


def run(c): return asyncio.run(c)


def test_two_zones_have_independent_values():
    m, _ = make(lux=2, lux2=300, rad=900)
    m.recompute()
    z1, z2 = m.values["zones"]["living_room"], m.values["zones"]["bedroom"]
    assert m.values["daylight_factor"] == 100
    assert z1["ambient"] != z2["ambient"]
    assert set(m.values["lamps"]) == set(m.cfg.lamps)
    assert m.values["lamps"]["bed_ceiling"]["target"] is not None


def test_controller_steps_by_10_per_zone_and_never_turns_on():
    m, hass = make(mode="Evening", lux=5, rad=0, lux2=5)
    hass.states[DEFAULT_LAMP_ENTITIES["top_right"]] = St("on", brightness=int(0.9 * 255))
    hass.states["light.bed_ceiling"] = St("on", brightness=int(0.5 * 255))
    run(m.controller_tick())
    calls = [c[2] for c in hass.services.calls if c[:2] == ("light", "turn_on")]
    assert sorted(c["entity_id"][0] for c in calls) == ["light.bed_ceiling", DEFAULT_LAMP_ENTITIES["top_right"]]
    by = {c["entity_id"][0]: c for c in calls}
    assert by[DEFAULT_LAMP_ENTITIES["top_right"]]["brightness_pct"] == 80   # 90 -> 80 (target 60, step capped)
    assert by["light.bed_ceiling"]["brightness_pct"] == 60                  # 50 -> 60 (target 100, step capped)
    assert all(c["transition"] == 5 for c in calls)
    assert not any(c[2].get("entity_id") == ["light.bed_night"] for c in hass.services.calls)


def test_shadow_mode_auto_switch_pause_and_master_switch():
    m, hass = make(mode="Evening", lux=5, rad=0, apply=False)
    ent = DEFAULT_LAMP_ENTITIES["top_right"]
    hass.states[ent] = St("on", brightness=230)
    run(m.controller_tick())
    assert [c[:2] for c in hass.services.calls] == [("logbook", "log")] and "would set" in hass.services.calls[0][2]["message"]
    m.state.apply = True; hass.services.calls.clear()
    m.state.auto["top_right"] = False; run(m.controller_tick()); assert not hass.services.calls
    m.state.auto["top_right"] = True; m.state.pause_until["top_right"] = NOW.timestamp() + 600
    run(m.controller_tick()); assert not hass.services.calls
    m.state.pause_until["top_right"] = 0; m.state.enabled = False
    run(m.controller_tick()); assert not hass.services.calls
    m.state.enabled = True; run(m.controller_tick()); assert len(hass.services.calls) == 1


def test_manual_change_pause_group_sharing_and_override_minutes():
    m, _ = make()
    old, manual = St("on", brightness=100), St("on", brightness=200)
    until = m.handle_lamp_change("bed_ceiling", old, manual)
    assert until == pytest.approx(NOW.timestamp() + 30 * 60)
    assert m.state.pause_until["bed_night"] == until                          # share_pause group
    assert m.handle_lamp_change("desk", old, manual) == pytest.approx(NOW.timestamp() + 30 * 60)
    assert m.state.pause_until["stick"] == 0                                  # group without share_pause: only the lamp
    m.state.last_set_ts = NOW.timestamp() - 10
    assert m.handle_lamp_change("desk", old, manual) is None                  # own change window
    m.state.last_set_ts = 0
    child = St("on", ctx=types.SimpleNamespace(id="c", parent_id="auto1"), brightness=200)
    assert m.handle_lamp_change("desk", old, child) is None
    m.set_param("global", "", "override_minutes", 90)
    assert m.handle_lamp_change("desk", old, manual) == pytest.approx(NOW.timestamp() + 90 * 60)


def test_openness_drops_after_confirm_ticks_per_zone():
    m, _ = make(lux=2, rad=900, lux2=500)
    m.recompute()
    for _ in range(6):
        m.openness_tick()
    assert m.values["zones"]["living_room"]["openness"] < 100
    assert m.values["zones"]["bedroom"]["openness"] == 100


def test_state_survives_restart_and_set_param_persists_config():
    store = MemoryStore()
    m, _ = make(store=store)
    m.state.pause_until["desk"] = NOW.timestamp() + 900
    m.state.auto["desk"] = False
    m.openness["bedroom"] = type(m.openness["bedroom"])(37.0, 2)
    m.set_param("lamp", "desk", "k", 0.77)
    m.mark_dirty(); run(m.async_flush())
    assert m.saved_configs and m.saved_configs[-1]["lamps"][3]["params"]["k"] == 0.77
    m2, _ = make(store=store)                       # new manager over the same store = HA restart
    m2.state.auto.clear(); m2.state.pause_until.clear()
    run(m2.async_load())
    assert m2.state.pause_until["desk"] == NOW.timestamp() + 900 and m2.state.auto["desk"] is False
    assert (m2.openness["bedroom"].value, m2.openness["bedroom"].low_count) == (37.0, 2)
    assert m2.state.enabled and m2.state.apply


def test_set_param_validation():
    m, _ = make()
    with pytest.raises(ValueError):
        m.set_param("lamp", "desk", "step_pct", 20)
    m.set_param("zone", "bedroom", "dark_lux_offset", 2)
    assert m.cfg.zone_param("bedroom", "dark_lux_offset") == 2 and m.cfg.zone_param("living_room", "dark_lux_offset") == 0
    m.set_param("zone", "bedroom", "dark_lux_offset", None)
    assert m.cfg.zone_param("bedroom", "dark_lux_offset") == 0


# ---- calibration service against a simulated room built on the fake hass

class SimLamps:
    """light.turn_on / turn_off update the fake states; lux = background + sum(k * pct)."""
    K = {"bed_ceiling": 0.3, "bed_night": 0.05}

    def __init__(self, hass, m, bg=0.4):
        self.hass, self.m, self.bg = hass, m, bg
        hass.services.async_call = self.call
        self.pct = {}

    async def call(self, domain, service, data, blocking=False, context=None):
        if domain != "light":
            return
        ents = data["entity_id"] if isinstance(data["entity_id"], list) else [data["entity_id"]]
        for e in ents:
            key = e.split(".")[1]
            if service == "turn_off":
                self.pct.pop(key, None); self.hass.states[e] = St("off")
            else:
                p = data.get("brightness_pct", round(data.get("brightness", 255) / 2.55))
                self.pct[key] = p; self.hass.states[e] = St("on", brightness=round(p * 2.55))
        lux = self.bg + sum(self.K[k] * p for k, p in self.pct.items())
        self.hass.states["sensor.bedroom_lux"] = St(str(round(lux, 3)))


def calib_setup():
    m, hass = make(mode="Day", lux2=0.4, rad=0)
    hass.states["sensor.general_sun_solar_elevation"] = St("-20")
    hass.states["input_select.living_room_light_mode"] = St("Night")
    sim = SimLamps(hass, m)
    m.sleep = lambda s: asyncio.sleep(0)
    return m, hass, sim


def test_zone_calibration_then_apply_writes_k_and_dark_offset():
    m, hass, sim = calib_setup()
    hass.states["light.bed_night"] = St("on", brightness=40)        # state to be restored afterwards
    async def go():
        await svc.async_calibrate(hass, m, "bedroom", 1, wait=True)
        return await svc.async_calibrate_apply(hass, m, "bedroom")
    ok = run(go())
    assert m.calibration_status["bedroom"] == "done"
    assert ok["bed_ceiling"] == pytest.approx(0.3, abs=0.002) and ok["bed_night"] == pytest.approx(0.05, abs=0.002)
    assert m.cfg.lamp_param("bed_ceiling", "k") == pytest.approx(0.3, abs=0.002)
    assert m.cfg.zone_param("bedroom", "dark_lux_offset") == pytest.approx(0.4, abs=0.01)
    assert hass.states["light.bed_night"].state == "on" and hass.states["light.bed_night"].attributes["brightness"] == pytest.approx(40, abs=2)
    assert hass.states["light.bed_ceiling"].state == "off"
    assert "bedroom" not in m.state.candidates and m.saved_configs


def record_notes(hass):
    notes, inner = [], hass.services.async_call
    async def call(domain, service, data, blocking=False, context=None):
        if domain == "persistent_notification":
            notes.append(data)
            return
        return await inner(domain, service, data, blocking=blocking, context=context)
    hass.services.async_call = call
    return notes


def test_calibration_refuses_only_for_technical_reasons():
    m, hass, _ = calib_setup()
    with pytest.raises(ValueError, match="no calibration candidates"):
        run(svc.async_calibrate_apply(hass, m, "bedroom"))
    with pytest.raises(ValueError, match="unknown zone"):
        run(svc.async_calibrate(hass, m, "garage", 1))

    async def twice():
        t = await svc.async_calibrate(hass, m, "bedroom", 1)
        with pytest.raises(ValueError, match="already running"):
            await svc.async_calibrate(hass, m, "bedroom", 1)
        await t
    run(twice())
    assert m.calibration_status["bedroom"] == "done"


@pytest.mark.parametrize("what", ["presence", "sun", "mode", "tv", "all"])
def test_bad_conditions_warn_but_calibration_runs(what, caplog):
    m, hass, sim = calib_setup()
    raw = m.cfg.to_dict()
    ops.set_media(raw, "bedroom", ["media_player.bed_tv"])
    m._replace_config(raw)
    hass.states["media_player.bed_tv"] = St("off")
    m.set_param("zone", "bedroom", "mode_entity", "input_select.living_room_light_mode")
    notes = record_notes(hass)
    bad = {"presence": ("presence",), "sun": ("sun is at 10",), "mode": ("not Night",), "tv": ("media_player.bed_tv",),
           "all": ("presence", "sun is at 10", "not Night", "media_player.bed_tv")}[what]
    if "presence" in bad:
        hass.states["binary_sensor.bedroom_presence"] = St("on")
    if "sun is at 10" in bad:
        hass.states["sensor.general_sun_solar_elevation"] = St("10")
    if "not Night" in bad:
        hass.states["input_select.living_room_light_mode"] = St("Day")
    if "media_player.bed_tv" in bad:
        hass.states["media_player.bed_tv"] = St("playing")
    with caplog.at_level("WARNING"):
        run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_status["bedroom"] == "done" and "bedroom" in m.state.candidates
    warns = m.calibration_warnings["bedroom"]
    for word in bad:
        assert any(word in w for w in warns), (word, warns)
    assert any(r.levelname == "WARNING" and "starts despite" in r.getMessage() and bad[0] in r.getMessage() for r in caplog.records)
    start = [n for n in notes if n["title"].startswith("Light calibration started")]
    assert len(start) == 1 and start[0]["notification_id"].endswith("_warning")
    assert "do not turn the lamps on or off" in start[0]["message"].lower() and "вручную" in start[0]["message"]
    assert all(word in start[0]["message"] for word in bad)
    assert not any("blocked" in n["message"] for n in notes)


def test_good_conditions_still_show_the_hands_off_caution(caplog):
    m, hass, _ = calib_setup()
    notes = record_notes(hass)
    with caplog.at_level("WARNING"):
        run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_warnings["bedroom"] == []
    assert any("do not turn the lamps on or off" in r.getMessage().lower() for r in caplog.records)
    assert any("do not turn the lamps on or off" in n["message"].lower() for n in notes)


def test_calibration_with_bright_background_finishes_with_warning():
    m, hass, sim = calib_setup()
    sim.bg = 9
    run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_status["bedroom"] == "done"
    assert any("not darkness" in w for w in m.calibration_warnings["bedroom"])
    assert m.state.candidates["bedroom"]["bg0"] == pytest.approx(9, abs=0.01)


def notes_with(notes, word): return [n for n in notes if word in n["title"]]


def test_single_lamp_without_growth_is_base_and_the_rest_is_measured():
    m, hass, sim = calib_setup()
    sim.K = {**sim.K, "bed_ceiling": 0.0}                           # the lamp next to a high sensor: no effect on the lux
    m.set_param("lamp", "bed_ceiling", "k", 0.25)                   # the last applied / hand-set k
    hass.states["light.bed_night"] = St("on", brightness=40)
    notes = record_notes(hass)
    run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_status["bedroom"] == "done_mixed"
    cand = m.state.candidates["bedroom"]
    assert cand["bed_ceiling"] == 0.25 and cand["_base_bed_ceiling"] == 1.0 and cand["bed_night"] == pytest.approx(0.05, abs=0.002)
    assert "bg0" in cand and "_base_bed_night" not in cand
    info = m.calibration_summary("bedroom")
    assert info["source"] == "mixed" and info["base_lamps"] == ["bed_ceiling"] and info["lamp_sources"] == {"bed_ceiling": "base", "bed_night": "measured"}
    base = notes_with(notes, "base value used")
    assert len(base) == 1 and base[0]["notification_id"].endswith("_base") and "bed_ceiling" in base[0]["message"]
    assert "calib_levels" in base[0]["message"] and "settle_s" in base[0]["message"] and "Базовое" in base[0]["message"]
    assert "BASE k (not measured)=0.25" in notes_with(notes, "Light calibration: bedroom")[0]["message"]
    assert hass.states["light.bed_night"].state == "on"             # no abort: lamps restored as always
    ok = run(svc.async_calibrate_apply(hass, m, "bedroom"))
    assert ok["bed_ceiling"] == 0.25 and ok["bed_night"] == pytest.approx(0.05, abs=0.002) and "bg0" in ok
    assert m.cfg.lamp_param("bed_ceiling", "k") == 0.25
    assert m.calibration_summary("bedroom")["applied"] is True
    assert len(notes_with(notes, "applied with base values")) == 1


def test_all_lamps_without_growth_keep_base_k_and_never_touch_dark_offset(caplog):
    m, hass, sim = calib_setup()
    sim.K = {"bed_ceiling": 0.0, "bed_night": 0.0}
    m.set_param("zone", "bedroom", "dark_lux_offset", 2)
    m.set_param("lamp", "bed_night", "k", 0.07)
    notes = record_notes(hass)
    with caplog.at_level("WARNING"):
        run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_status["bedroom"] == "done_base"
    cand = m.state.candidates["bedroom"]
    assert "bg0" not in cand and cand["bed_night"] == 0.07 and cand["bed_ceiling"] == m.cfg.lamp_param("bed_ceiling", "k") == 0.1
    assert all(cand[k] > 0 for k in ("bed_ceiling", "bed_night"))
    assert m.calibration_summary("bedroom")["source"] == "base" and sorted(m.calibration_summary("bedroom")["base_lamps"]) == ["bed_ceiling", "bed_night"]
    assert "no growth" in caplog.text
    msg = notes_with(notes, "base value used")[0]["message"]
    assert "whole zone is calibrated 'by base'" in msg and "dark_lux_offset is NOT proposed" in msg and "по базе" in msg
    ok = run(svc.async_calibrate_apply(hass, m, "bedroom"))
    assert "bg0" not in ok and ok == {"bed_ceiling": 0.1, "bed_night": 0.07}
    assert m.cfg.zone_param("bedroom", "dark_lux_offset") == 2          # untouched
    assert m.cfg.lamp_param("bed_night", "k") == 0.07
    applied = notes_with(notes, "applied with base values")[0]["message"]
    assert "NOT written" in applied and "НЕ записан" in applied


def test_base_summary_is_derived_from_stored_candidates_after_restart():
    m, hass, sim = calib_setup()
    sim.K = {**sim.K, "bed_night": 0.0}
    run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    m.calibration_info.clear()                                           # as after a restart: only the candidates are persisted
    info = m.calibration_summary("bedroom")
    assert info["source"] == "mixed" and info["base_lamps"] == ["bed_night"] and info["lamp_sources"]["bed_ceiling"] == "measured"
    m.state.candidates.clear(); m.calibration_info.clear()
    assert m.calibration_summary("bedroom") == {}


def test_no_illuminance_reading_still_aborts_the_zone_calibration():
    m, hass, sim = calib_setup()
    hass.states["sensor.bedroom_lux"] = St("unavailable")
    sim_call = hass.services.async_call
    async def call(domain, service, data, blocking=False, context=None):
        await sim_call(domain, service, data, blocking=blocking, context=context)
        hass.states["sensor.bedroom_lux"] = St("unavailable")
    hass.services.async_call = call
    hass.states["light.bed_night"] = St("on", brightness=40)
    run(svc.async_calibrate(hass, m, "bedroom", 1, wait=True))
    assert m.calibration_status["bedroom"] == "aborted" and "bedroom" not in m.state.candidates
    assert hass.states["light.bed_night"].state == "on"


def test_open_k_service():
    m, hass = make(mode="Day", lux=300, rad=600)
    k = run(svc.async_calibrate_open_k(hass, m, "living_room"))
    assert m.cfg.zone_param("living_room", "open_k") == k and k == round(300 / m.values["daylight_factor"], 2)
    hass.states[DEFAULT_LAMP_ENTITIES["desk"]] = St("on", brightness=100)
    with pytest.raises(ValueError, match="lamp is on"):
        run(svc.async_calibrate_open_k(hass, m, "living_room"))

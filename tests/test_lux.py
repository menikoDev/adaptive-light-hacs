"""A zone turns lamps on only when someone is present and the estimate is below the threshold."""
from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.persistence import MemoryStore

from test_presence import Home, _saw, run


def raw(presence=("binary_sensor.p",), timeout=30, lamps=("light.a", "light.b"), **extra):
    zone = {
        "key": "hall",
        "name": "Hall",
        "lamps": list(lamps),
        "presence": list(presence),
        "off_timeout": timeout,
    }
    zone.update(extra)
    return {"version": 5, "zones": [zone]}


def ready(home, cfg, presence="on", lamps="off"):
    home.states.set("binary_sensor.p", presence)
    for lamp in cfg["zones"][0]["lamps"]:
        home.states.set(lamp, lamps)
    return AdaptiveLightManager(home, cfg, now=home.now)


def test_lux_below_40_turns_on_with_presence():
    home = Home()
    home.states.set("sensor.room", "30")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert mgr.estimate("hall") == 30


def test_the_band_between_40_and_80_does_not_change_the_lamps():
    home = Home()
    home.states.set("sensor.room", "40")
    cfg = raw(lux="sensor.room", threshold=100)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("sensor.room", "80")
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_no_presence_never_turns_on():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg, presence="off")
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_a_zone_without_a_lux_sensor_uses_radiation_times_0_6():
    home = Home()
    home.outdoor = {"radiation": "sensor.rad", "factor": 0.6}
    home.states.set("sensor.rad", "100")
    cfg = raw(threshold=50)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 60
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []

    home.states.set("sensor.rad", "50")
    assert mgr.estimate("hall") == 30
    assert run(mgr.apply("hall")) == "wait"
    home.t = 120
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_empty_outdoor_stays_on_presence_only():
    home = Home()
    home.outdoor = {"radiation": "", "factor": 0.6}
    cfg = raw(threshold=50)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") is None
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_threshold_zero_ignores_light_and_does_not_morning_off():
    home = Home()
    home.states.set("sensor.room", "10000")
    cfg = raw(lux="sensor.room", threshold=0)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []
    assert mgr.manual_off == set()


def test_estimate_above_the_threshold_turns_lamps_off_without_manual_off():
    home = Home()
    home.states.set("sensor.room", "20")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.states.set("sensor.room", "90")
    assert run(mgr.apply("hall")) == "wait"
    home.t = 599
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []
    home.t = 600
    assert run(mgr.apply("hall")) == "lux"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == set()
    home.states.set("sensor.room", "10")
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "wait"
    home.t = 720
    assert run(mgr.apply("hall")) == "on"


def test_manual_off_and_the_timeout_still_work():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, timeout=30)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.a", "off")
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.b"]})]
    assert "light.a" in mgr.manual_off
    _saw(mgr, home, "light.b", "on")

    home.states.set("binary_sensor.p", "off")
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "wait"
    home.t = 30
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.b"]})]
    _saw(mgr, home, "light.b", "off")
    assert "light.b" not in mgr.manual_off


def test_already_occupied_and_below_threshold_turns_on_without_a_presence_edge():
    home = Home()
    home.states.set("sensor.room", "30")
    cfg = raw(lux="sensor.room", threshold=100, timeout=300)
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = AdaptiveLightManager(home, cfg, now=home.now)
    mgr.note_lamp_change("light.a", "off")
    mgr.note_lamp_change("light.b", "off")
    assert mgr.manual_off == set()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}


def test_estimate_dropping_below_threshold_turns_on_while_still_occupied():
    home = Home()
    home.states.set("sensor.room", "150")
    cfg = raw(lux="sensor.room", threshold=100)
    mgr = ready(home, cfg, lamps="on")
    assert run(mgr.apply("hall")) == "wait"
    home.t = 600
    assert run(mgr.apply("hall")) == "lux"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    mgr.note_lamp_change("light.a", "off")
    mgr.note_lamp_change("light.b", "off")
    assert mgr.manual_off == set()
    home.services.calls.clear()
    home.states.set("sensor.room", "30")
    assert run(mgr.apply("hall")) == "wait"
    home.t = 720
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_a_manual_off_saved_before_the_fix_does_not_keep_the_lamps_off():
    home = Home()
    home.states.set("sensor.room", "20")
    cfg = raw(lux="sensor.room", threshold=100, timeout=300)
    store = MemoryStore({
        "version": 3,
        "enabled": {"hall": True},
        "manual_off": ["light.a", "light.b"],
        "night_hold": [],
    })
    mgr = ready(home, cfg)
    mgr.store = store
    run(mgr.async_load())
    assert mgr.manual_off == set()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_a_real_manual_off_saved_after_the_fix_still_blocks():
    home = Home()
    home.states.set("sensor.room", "20")
    cfg = raw(lux="sensor.room", threshold=100)
    store = MemoryStore()
    mgr = ready(home, cfg, lamps="on")
    mgr.store = store
    _saw(mgr, home, "light.a", "off")
    assert mgr.manual_off == {"light.a"}
    saved = mgr._snapshot()
    assert saved["manual_off_epoch"] == 2 and saved["manual_off"] == ["light.a"]

    again = Home()
    again.states.set("sensor.room", "20")
    again.states.set("binary_sensor.p", "on")
    again.states.set("light.a", "off")
    again.states.set("light.b", "off")
    restored = AdaptiveLightManager(again, cfg, store=MemoryStore(saved), now=again.now)
    run(restored.async_load())
    assert restored.manual_off == {"light.a"}
    assert run(restored.apply("hall")) == "on"
    assert again.services.calls == [("light", "turn_on", {"entity_id": ["light.b"]})]


def test_a_room_sensor_is_not_added_to_outdoor_radiation():
    home = Home()
    home.outdoor = {"radiation": "sensor.rad", "factor": 0.6}
    home.states.set("sensor.room", "40")
    home.states.set("sensor.rad", "1000")
    cfg = raw(lux="sensor.room", lux_coefficient=2, threshold=100)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 80
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_a_short_spike_does_not_toggle_the_lamps():
    home = Home()
    home.states.set("sensor.room", "60")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg, lamps="on")
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("sensor.room", "20")
    assert run(mgr.apply("hall")) == "wait"
    home.t = 119
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("sensor.room", "55")
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("sensor.room", "100")
    home.t = 200
    assert run(mgr.apply("hall")) == "wait"
    home.t = 200 + 599
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("sensor.room", "70")
    home.t = 2000
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_saving_the_zone_clears_a_stuck_manual_off_and_turns_on_below_40():
    home = Home()
    home.states.set("sensor.room", "25")
    cfg = raw(lux="sensor.room", threshold=100, manual_clear=1)
    store = MemoryStore({
        "version": 3,
        "enabled": {"hall": True},
        "manual_off": ["light.a", "light.b"],
        "night_hold": [],
        "manual_off_epoch": 2,
    })
    mgr = ready(home, cfg)
    mgr.store = store
    run(mgr.async_load())
    assert mgr.manual_off == set()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}

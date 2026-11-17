"""Lux on is immediate below the upper threshold. Lux off waits for a 10-minute average below the lower one."""
from adaptive_light.control import lower_lux
from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.model import Config
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


def test_current_lux_below_the_upper_threshold_turns_on_immediately():
    home = Home()
    home.states.set("sensor.room", "40")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}
    home.t = 1
    home.services.calls.clear()
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []


def test_a_drop_below_the_upper_threshold_does_not_wait():
    home = Home()
    home.states.set("sensor.room", "80")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []
    home.t = 1
    home.states.set("sensor.room", "30")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_lux_above_the_upper_threshold_does_not_turn_on():
    home = Home()
    home.states.set("sensor.room", "80")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_no_presence_never_turns_on():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg, presence="off")
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_lux_between_lower_and_upper_does_not_turn_the_light_off():
    home = Home()
    home.states.set("sensor.room", "40")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg)
    assert lower_lux(50, 15) == 35
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.t = 600
    assert run(mgr.apply("hall")) == "on"
    home.t = 900
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []
    assert mgr.manual_off == set()


def test_off_only_when_the_ten_minute_average_is_below_the_lower_threshold():
    home = Home()
    home.states.set("sensor.room", "20")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.t = 599
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []
    home.t = 600
    assert run(mgr.apply("hall")) == "lux"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == set()


def test_a_short_spike_does_not_turn_the_light_off():
    home = Home()
    home.states.set("sensor.room", "40")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15)
    mgr = ready(home, cfg, lamps="on")
    assert run(mgr.apply("hall")) == "on"
    home.services.calls.clear()
    home.t = 600
    home.states.set("sensor.room", "10")
    assert run(mgr.apply("hall")) == "on"
    home.t = 630
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []
    home.t = 1200
    assert run(mgr.apply("hall")) == "lux"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]


def test_gap_zero_means_the_lower_threshold_equals_the_upper():
    assert lower_lux(50, 0) == 50
    loaded = Config.from_dict(raw(lux="sensor.room", threshold=50))
    assert loaded.zones["hall"]["lux_gap"] == 0
    assert lower_lux(loaded.zones["hall"]["threshold"], loaded.zones["hall"]["lux_gap"]) == 50

    home = Home()
    home.states.set("sensor.room", "40")
    mgr = ready(home, raw(lux="sensor.room", threshold=50))
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.t = 600
    assert run(mgr.apply("hall")) == "lux"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == set()


def test_threshold_zero_ignores_light_and_does_not_turn_off_for_lux():
    home = Home()
    home.states.set("sensor.room", "10000")
    cfg = raw(lux="sensor.room", threshold=0, lux_gap=15)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.t = 600
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []
    assert mgr.manual_off == set()


def test_a_zone_without_a_lux_sensor_uses_radiation_times_0_6():
    home = Home()
    home.outdoor = {"radiation": "sensor.rad", "factor": 0.6}
    home.states.set("sensor.rad", "100")
    cfg = raw(threshold=50, lux_gap=0)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 60
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []

    home.states.set("sensor.rad", "50")
    assert mgr.estimate("hall") == 30
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


def test_manual_off_and_the_timeout_still_work():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, lux_gap=15, timeout=30)
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


def test_already_occupied_and_below_the_upper_threshold_turns_on_without_a_presence_edge():
    home = Home()
    home.states.set("sensor.room", "30")
    cfg = raw(lux="sensor.room", threshold=100, lux_gap=15, timeout=300)
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = AdaptiveLightManager(home, cfg, now=home.now)
    mgr.note_lamp_change("light.a", "off")
    mgr.note_lamp_change("light.b", "off")
    assert mgr.manual_off == set()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_a_manual_off_saved_before_the_fix_does_not_keep_the_lamps_off():
    home = Home()
    home.states.set("sensor.room", "20")
    cfg = raw(lux="sensor.room", threshold=100, lux_gap=15, timeout=300)
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
    cfg = raw(lux="sensor.room", threshold=100, lux_gap=15)
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


def test_saving_the_zone_clears_a_stuck_manual_off_and_turns_on_below_the_upper_threshold():
    home = Home()
    home.states.set("sensor.room", "25")
    cfg = raw(lux="sensor.room", threshold=100, lux_gap=15, manual_clear=1)
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


def test_a_room_sensor_is_not_added_to_outdoor_radiation():
    home = Home()
    home.outdoor = {"radiation": "sensor.rad", "factor": 0.6}
    home.states.set("sensor.room", "30")
    home.states.set("sensor.rad", "1000")
    cfg = raw(lux="sensor.room", lux_coefficient=2, threshold=100, lux_gap=15)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 60
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]

"""With presence, a weighted lux average below the threshold turns on. Rising above turns off."""
from adaptive_light.control import weighted_average
from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.model import Config
from adaptive_light.persistence import MemoryStore

from test_events import _logger, _stop
from test_presence import Home, _saw, run


def raw(presence=("binary_sensor.p",), timeout=30, lamps=("light.a", "light.b"), **extra):
    zone = {
        "key": "hall",
        "name": "Hall",
        "lamps": list(lamps),
        "presence": list(presence),
        "off_timeout": timeout,
        "entry_delay": 0,
    }
    zone.update(extra)
    return {"version": 5, "zones": [zone]}


def ready(home, cfg, presence="on", lamps="off"):
    home.states.set("binary_sensor.p", presence)
    for lamp in cfg["zones"][0]["lamps"]:
        home.states.set(lamp, lamps)
    return AdaptiveLightManager(home, cfg, now=home.now)


def test_newer_samples_weigh_more_than_the_time_they_cover():
    # Nine minutes at 0 and the latest minute at 100. Equal time would average 10.
    samples = [(0.0, 0.0), (540.0, 100.0)]
    assert weighted_average(samples, 600.0, 600.0) == 19.0


def test_a_missing_window_is_ten_minutes_and_the_old_gap_is_ignored():
    loaded = Config.from_dict(raw(lux="sensor.room", threshold=50, lux_gap=15))
    assert loaded.zones["hall"]["window"] == 10
    assert "lux_gap" not in loaded.zones["hall"]
    assert Config.from_dict(raw(window=1)).zones["hall"]["window"] == 1
    assert Config.from_dict(raw(window=0)).zones["hall"]["window"] == 10


def test_current_lux_below_the_threshold_turns_on_immediately():
    home = Home()
    home.states.set("sensor.room", "40")
    cfg = raw(lux="sensor.room", threshold=50)
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


def test_a_drop_below_the_threshold_turns_on():
    home = Home()
    home.states.set("sensor.room", "80")
    cfg = raw(lux="sensor.room", threshold=50, window=1)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == []
    home.t = 1
    home.states.set("sensor.room", "30")
    run(mgr.apply("hall"))
    home.t = 70
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_lux_above_the_threshold_does_not_turn_on():
    home = Home()
    home.states.set("sensor.room", "80")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == []


def test_no_presence_never_turns_on():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg, presence="off")
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []


def test_a_low_average_while_the_room_is_occupied_does_not_turn_the_lamps_off():
    """The 22:38 case: estimate near 0, room occupied, must not log average-below-lower."""
    home = Home()
    home.states.set("sensor.room", "0")
    cfg = raw(lux="sensor.room", threshold=50)
    mgr = ready(home, cfg, lamps="on")
    log, cap = _logger()
    try:
        assert run(mgr.apply("hall")) == "on"
        home.t = 600
        home.states.set("sensor.room", "0.2")
        assert run(mgr.apply("hall")) == "on"
    finally:
        _stop(log, cap)
    assert home.services.calls == []
    assert mgr.manual_off == set()
    assert not any("reason=average-below-lower" in line for line in cap.lines)
    assert not any(call[1] == "turn_off" for call in home.services.calls)


def test_a_rising_average_above_the_threshold_turns_the_lamps_off():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, window=1)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()
    home.t = 1
    home.states.set("sensor.room", "400")
    assert run(mgr.apply("hall")) == "on"
    home.t = 70
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}
    assert mgr.manual_off == set()


def test_full_window_below_the_threshold_still_turns_on():
    home = Home()
    home.states.set("sensor.room", "40")
    mgr = ready(home, raw(lux="sensor.room", threshold=50))
    assert run(mgr.apply("hall")) == "on"
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    home.services.calls.clear()
    home.t = 600
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert mgr.manual_off == set()


def test_threshold_zero_ignores_light_and_does_not_turn_off_for_lux():
    home = Home()
    home.states.set("sensor.room", "10000")
    cfg = raw(lux="sensor.room", threshold=0)
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
    cfg = raw(threshold=50)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 60
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == []

    home.states.set("sensor.rad", "50")
    home.t = 1
    run(mgr.apply("hall"))
    home.t = 700
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


def test_a_falling_average_turns_the_lamps_on_with_their_last_color():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, window=1)
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off", attributes={"color_mode": "rgb", "rgb_color": (9, 8, 7), "brightness": 120})
    home.states.set("light.b", "off")
    mgr = AdaptiveLightManager(home, cfg, now=home.now)
    assert run(mgr.apply("hall")) == "on"
    assert ("light", "turn_on", {"entity_id": ["light.a"], "rgb_color": [9, 8, 7], "brightness": 120}) in home.services.calls
    assert ("light", "turn_on", {"entity_id": ["light.b"]}) in home.services.calls
    _saw(mgr, home, "light.a", "on", {"color_mode": "rgb", "rgb_color": (9, 8, 7), "brightness": 120})
    _saw(mgr, home, "light.a", "off")
    assert "light.a" in mgr.manual_off
    home.services.calls.clear()
    home.t = 1
    assert run(mgr.apply("hall")) == "on"
    assert all("light.a" not in call[2].get("entity_id", []) for call in home.services.calls)
    assert mgr.manual_off == {"light.a"}


def test_a_hand_off_stays_off_when_the_average_falls_back_below_the_threshold():
    home = Home()
    home.states.set("sensor.room", "10")
    cfg = raw(lux="sensor.room", threshold=50, window=1)
    mgr = ready(home, cfg)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    _saw(mgr, home, "light.a", "off")
    assert mgr.manual_off == {"light.a"}

    home.t = 1
    home.states.set("sensor.room", "400")
    assert run(mgr.apply("hall")) == "on"
    home.t = 70
    assert run(mgr.apply("hall")) == "off"
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == {"light.a"}

    home.t = 71
    home.states.set("sensor.room", "10")
    run(mgr.apply("hall"))
    home.t = 140
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.b"]})]
    assert mgr.manual_off == {"light.a"}


def test_already_occupied_and_below_the_threshold_turns_on_without_a_presence_edge():
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


def test_saving_the_zone_clears_a_stuck_manual_off_and_turns_on_below_the_threshold():
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


def test_a_room_sensor_is_not_added_to_outdoor_radiation():
    home = Home()
    home.outdoor = {"radiation": "sensor.rad", "factor": 0.6}
    home.states.set("sensor.room", "30")
    home.states.set("sensor.rad", "1000")
    cfg = raw(lux="sensor.room", lux_coefficient=2, threshold=100)
    mgr = ready(home, cfg)
    assert mgr.estimate("hall") == 60
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]

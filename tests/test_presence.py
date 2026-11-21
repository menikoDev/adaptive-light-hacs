"""Presence turns the zone lamps on and off. Nothing else is sent to the light."""
import asyncio
from datetime import datetime

from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.model import normalise


class _State:
    def __init__(self, state, attributes=None, context=None):
        self.state = state
        self.attributes = attributes or {}
        self.context = context


class _States:
    def __init__(self):
        self._data = {}

    def get(self, entity):
        return self._data.get(entity)

    def set(self, entity, state, attributes=None, context=None):
        self._data[entity] = _State(state, attributes, context)


class _Services:
    def __init__(self):
        self.calls = []
        self._available: set[tuple[str, str]] = set()

    def has_service(self, domain, service):
        return (domain, service) in self._available

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, dict(data)))


class Home:
    def __init__(self):
        self.states = _States()
        self.services = _Services()
        self.t = 0.0

    def now(self):
        return self.t


def zone(presence, timeout=30, lamps=None):
    return {
        "version": 5,
        "zones": [{
            "key": "hall",
            "name": "Hall",
            "lamps": lamps or ["light.a", "light.b"],
            "presence": presence,
            "off_timeout": timeout,
        }],
    }


def manager(home, presence, timeout=30):
    return AdaptiveLightManager(home, zone(presence, timeout), now=home.now)


def run(coro):
    return asyncio.run(coro)


def test_any_sensor_turns_the_lamps_on_and_one_sensor_keeps_them_on():
    home = Home()
    home.states.set("binary_sensor.one", "off")
    home.states.set("binary_sensor.two", "off")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = manager(home, ["binary_sensor.one", "binary_sensor.two"], timeout=30)
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []

    home.states.set("binary_sensor.two", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")

    home.t = 1000
    home.states.set("binary_sensor.two", "off")
    home.states.set("binary_sensor.one", "on")
    assert run(mgr.apply("hall")) == "on"
    assert len(home.services.calls) == 1
    home.t = 1000 + 30
    assert run(mgr.apply("hall")) == "on"
    assert [call[1] for call in home.services.calls] == ["turn_on"]


def test_all_off_plus_the_timeout_turns_the_lamps_off():
    home = Home()
    home.states.set("binary_sensor.one", "on")
    home.states.set("binary_sensor.two", "off")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = manager(home, ["binary_sensor.one", "binary_sensor.two"], timeout=30)
    run(mgr.apply("hall"))
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    home.services.calls.clear()

    home.t = 500
    home.states.set("binary_sensor.one", "off")
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []
    home.t = 529
    assert run(mgr.apply("hall")) == "wait"
    assert home.services.calls == []
    home.t = 530
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    for _domain, _service, data in home.services.calls:
        assert set(data) == {"entity_id"}


def test_a_single_presence_sensor_still_turns_lamps_on_and_off():
    home = Home()
    home.states.set("binary_sensor.only", "off")
    home.states.set("light.a", "off")
    mgr = manager(home, ["binary_sensor.only"], timeout=10)
    assert run(mgr.apply("hall")) == "wait"
    home.states.set("binary_sensor.only", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    home.services.calls.clear()
    home.t = 20
    home.states.set("binary_sensor.only", "off")
    assert run(mgr.apply("hall")) == "wait"
    home.t = 30
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]


def _saw(mgr, home, entity, state):
    home.states.set(entity, state)
    mgr.note_lamp_change(entity, state)


def test_an_external_turn_off_blocks_only_that_lamp():
    home = Home()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = manager(home, ["binary_sensor.p"], timeout=10)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")

    _saw(mgr, home, "light.a", "off")
    assert mgr.manual_off == {"light.a"}
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []

    home.states.set("binary_sensor.p", "off")
    home.t = 0
    assert run(mgr.apply("hall")) == "wait"
    home.t = 10
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.b"]})]
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == {"light.a"}

    home.services.calls.clear()
    home.states.set("binary_sensor.p", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.b"]})]


def test_the_zones_own_turn_off_does_not_block_the_next_presence():
    home = Home()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = manager(home, ["binary_sensor.p"], timeout=10)
    run(mgr.apply("hall"))
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()

    home.states.set("binary_sensor.p", "off")
    home.t = 0
    run(mgr.apply("hall"))
    home.t = 10
    assert run(mgr.apply("hall")) == "off"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == set()

    home.services.calls.clear()
    home.states.set("binary_sensor.p", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_an_external_turn_on_clears_the_block():
    home = Home()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "on")
    mgr = manager(home, ["binary_sensor.p"], timeout=10)
    run(mgr.apply("hall"))
    _saw(mgr, home, "light.a", "on")
    home.services.calls.clear()

    _saw(mgr, home, "light.a", "off")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []

    _saw(mgr, home, "light.a", "on")
    assert mgr.manual_off == set()
    home.states.set("binary_sensor.p", "off")
    home.t = 0
    run(mgr.apply("hall"))
    home.t = 10
    assert run(mgr.apply("hall")) == "off"
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert "light.a" not in mgr.manual_off

    home.services.calls.clear()
    home.states.set("binary_sensor.p", "on")
    assert run(mgr.apply("hall")) == "on"
    assert "light.a" in home.services.calls[-1][2]["entity_id"]


class Clock:
    def __init__(self, hour=3):
        self.t = 0.0
        self.hour = hour
        self.minute = 0

    def now(self):
        return self.t

    def local(self):
        return datetime(2026, 11, 15, self.hour, self.minute)


def _night_manager(home, clock, presence=None, night=True, key="hall", lamps=None, name="Hall", start="02:00", end="06:00", still=60):
    raw = zone(presence or ["binary_sensor.p"], lamps=lamps)
    raw["zones"][0]["key"] = key
    raw["zones"][0]["name"] = name
    raw["zones"][0]["night"] = night
    raw["zones"][0]["night_start"] = start
    raw["zones"][0]["night_end"] = end
    raw["zones"][0]["night_still"] = still
    return AdaptiveLightManager(home, raw, now=clock.now, local_now=clock.local)


def test_night_stillness_turns_the_lamps_off_until_the_next_movement():
    home, clock = Home(), Clock()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    home.states.set("light.b", "off")
    mgr = _night_manager(home, clock)
    assert run(mgr.apply("hall")) == "on"
    _saw(mgr, home, "light.a", "on")
    _saw(mgr, home, "light.b", "on")
    home.services.calls.clear()

    clock.t = 59 * 60
    clock.minute = 59
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []

    clock.t = 60 * 60
    clock.hour, clock.minute = 4, 0
    assert run(mgr.apply("hall")) == "night"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    assert mgr.manual_off == set()

    home.services.calls.clear()
    clock.hour = 7
    clock.t = 5 * 3600
    assert run(mgr.apply("hall")) == "night"
    assert home.services.calls == []

    home.states.set("binary_sensor.p", "off")
    run(mgr.apply("hall"))
    home.states.set("binary_sensor.p", "on")
    home.services.calls.clear()
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_without_night_mode_lying_still_keeps_the_lamps_on():
    home, clock = Home(), Clock()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    mgr = _night_manager(home, clock, night=False)
    assert run(mgr.apply("hall")) == "on"
    home.services.calls.clear()
    clock.t = 3 * 3600
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []


def test_a_saved_overnight_window_is_the_one_that_counts():
    """22:00–05:00 is not the default 02:00–06:00. The switch off leaves those times idle."""
    home, clock = Home(), Clock(hour=23)
    clock.minute = 0
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    mgr = _night_manager(home, clock, start="22:00", end="05:00", still=60)
    assert run(mgr.apply("hall")) == "on"

    clock.t = 60 * 60
    clock.hour, clock.minute = 0, 0
    assert run(mgr.apply("hall")) == "night"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")

    home.states.set("binary_sensor.p", "off")
    run(mgr.apply("hall"))
    home.states.set("binary_sensor.p", "on")
    home.services.calls.clear()
    clock.hour, clock.minute = 1, 0
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]

    home.services.calls.clear()
    clock.hour, clock.minute = 12, 0
    clock.t = 13 * 3600
    day = _night_manager(home, clock, start="22:00", end="05:00", still=60)
    assert run(day.apply("hall")) == "on"
    _saw(day, home, "light.a", "on")
    _saw(day, home, "light.b", "on")
    home.services.calls.clear()
    clock.t += 3 * 3600
    assert run(day.apply("hall")) == "on"
    assert home.services.calls == []

    clock.hour, clock.minute = 23, 30
    clock.t += 60 * 60
    idle = _night_manager(home, clock, night=False, start="22:00", end="05:00", still=60)
    assert run(idle.apply("hall")) == "on"
    _saw(idle, home, "light.a", "on")
    _saw(idle, home, "light.b", "on")
    home.services.calls.clear()
    clock.t += 3 * 3600
    assert run(idle.apply("hall")) == "on"
    assert home.services.calls == []


def test_presence_stuck_on_with_no_further_changes_times_out():
    """A sensor that stays on is not activity. Re-reading it must not restart the still period."""
    home, clock = Home(), Clock()
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    mgr = _night_manager(home, clock)
    assert run(mgr.apply("hall")) == "on"
    home.services.calls.clear()
    for minutes in (10, 30, 59):
        clock.t = minutes * 60
        clock.minute = minutes % 60
        assert run(mgr.apply("hall")) == "on"
        assert home.services.calls == []
    clock.t = 60 * 60
    clock.hour, clock.minute = 4, 0
    assert run(mgr.apply("hall")) == "night"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]


def test_one_sensor_change_resets_the_night_timer():
    """One presence sensor stays on. One state change restarts the still period and brings the lamps back."""
    home, clock = Home(), Clock()
    home.states.set("binary_sensor.sofa", "on")
    home.states.set("binary_sensor.door", "off")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    mgr = _night_manager(home, clock, presence=["binary_sensor.sofa", "binary_sensor.door"])
    assert run(mgr.apply("hall")) == "on"
    home.services.calls.clear()

    clock.t = 50 * 60
    home.states.set("binary_sensor.door", "on")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []

    clock.t = 60 * 60
    clock.hour, clock.minute = 4, 0
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []

    clock.t = 50 * 60 + 60 * 60
    clock.hour, clock.minute = 4, 50
    assert run(mgr.apply("hall")) == "night"
    _saw(mgr, home, "light.a", "off")
    _saw(mgr, home, "light.b", "off")
    home.services.calls.clear()

    home.states.set("binary_sensor.door", "off")
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a", "light.b"]})]


def test_a_change_in_another_zone_resets_the_night_timer():
    """A presence change on another entry restarts this zone and turns its lamps back on."""
    from adaptive_light.const import DOMAIN

    home, clock = Home(), Clock()
    home.data = {}
    home.states.set("binary_sensor.p", "on")
    home.states.set("binary_sensor.k", "off")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    home.states.set("light.k", "off")
    hall = _night_manager(home, clock)
    kitchen_raw = zone(["binary_sensor.k"], lamps=["light.k"])
    kitchen_raw["zones"][0]["key"] = "kitchen"
    kitchen_raw["zones"][0]["name"] = "Kitchen"
    kitchen = AdaptiveLightManager(home, kitchen_raw, now=clock.now, local_now=clock.local)
    home.data[DOMAIN] = {"hall": hall, "kitchen": kitchen}

    assert run(hall.apply("hall")) == "on"
    assert run(kitchen.apply("kitchen")) == "wait"
    home.services.calls.clear()

    clock.t = 60 * 60
    clock.hour, clock.minute = 4, 0
    assert run(hall.apply("hall")) == "night"
    _saw(hall, home, "light.a", "off")
    _saw(hall, home, "light.b", "off")
    home.services.calls.clear()

    home.states.set("binary_sensor.k", "on")
    assert run(kitchen.apply("kitchen")) == "on"
    assert ("light", "turn_on", {"entity_id": ["light.a", "light.b"]}) in home.services.calls
    assert "hall" not in hall._night_hold
    _saw(hall, home, "light.a", "on")
    _saw(hall, home, "light.b", "on")

    home.services.calls.clear()
    clock.t = 60 * 60 + 59 * 60
    clock.hour, clock.minute = 4, 59
    assert run(hall.apply("hall")) == "on"
    assert home.services.calls == []


def test_outside_the_night_window_stillness_does_not_turn_the_lamps_off():
    home, clock = Home(), Clock(hour=12)
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "on")
    home.states.set("light.b", "on")
    mgr = _night_manager(home, clock)
    assert run(mgr.apply("hall")) == "on"
    home.services.calls.clear()
    clock.t = 3 * 3600
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == []


def test_night_mode_does_not_touch_another_zone():
    home, clock = Home(), Clock()
    hall = zone(["binary_sensor.p"])["zones"][0]
    hall.update(night=True, night_start="02:00", night_end="06:00", night_still=60)
    raw = {"version": 5, "zones": [hall, {
        "key": "kitchen", "name": "Kitchen", "lamps": ["light.k"],
        "presence": ["binary_sensor.k"], "off_timeout": 30,
    }]}
    for entity, state in (
        ("binary_sensor.p", "on"), ("binary_sensor.k", "on"),
        ("light.a", "on"), ("light.b", "on"), ("light.k", "on"),
    ):
        home.states.set(entity, state)
    mgr = AdaptiveLightManager(home, raw, now=clock.now, local_now=clock.local)
    run(mgr.apply("hall"))
    run(mgr.apply("kitchen"))
    home.services.calls.clear()
    clock.t = 3600
    clock.hour = 4
    assert run(mgr.apply("hall")) == "night"
    assert run(mgr.apply("kitchen")) == "on"
    assert home.services.calls == [("light", "turn_off", {"entity_id": ["light.a", "light.b"]})]


def test_an_old_rule_config_is_dropped():
    old = {
        "version": 3,
        "rules": [{"zone": "living_room", "lux_below": 40, "min": 0, "max": 80, "gain": 1, "evening_mult": 1}],
        "zones": [{"key": "living_room", "name": "Living room", "params": {}}],
        "groups": [{"key": "task"}],
        "sensors": [{"role": "illuminance", "entity": "sensor.lux"}],
    }
    assert normalise(old) == {"version": 5, "zones": []}

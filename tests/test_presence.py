"""Presence turns the zone lamps on and off. Nothing else is sent to the light."""
import asyncio
from datetime import datetime

from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.model import normalise


class _State:
    def __init__(self, state):
        self.state = state


class _States:
    def __init__(self):
        self._data = {}

    def get(self, entity):
        return self._data.get(entity)

    def set(self, entity, state):
        self._data[entity] = _State(state)


class _Services:
    def __init__(self):
        self.calls = []

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


def _night_manager(home, clock, presence=None, night=True, key="hall", lamps=None, name="Hall"):
    raw = zone(presence or ["binary_sensor.p"], lamps=lamps)
    raw["zones"][0]["key"] = key
    raw["zones"][0]["name"] = name
    raw["zones"][0]["night"] = night
    raw["zones"][0]["night_start"] = "02:00"
    raw["zones"][0]["night_end"] = "06:00"
    raw["zones"][0]["night_still"] = 60
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

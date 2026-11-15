"""Presence turns the zone lamps on and off. Nothing else is sent to the light."""
import asyncio

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


def test_an_old_rule_config_is_dropped():
    old = {
        "version": 3,
        "rules": [{"zone": "living_room", "lux_below": 40, "min": 0, "max": 80, "gain": 1, "evening_mult": 1}],
        "zones": [{"key": "living_room", "name": "Living room", "params": {}}],
        "groups": [{"key": "task"}],
        "sensors": [{"role": "illuminance", "entity": "sensor.lux"}],
    }
    assert normalise(old) == {"version": 5, "zones": []}

"""Each zone is its own entry. The four fields are the only window."""
import asyncio
import importlib.util
import os
import sys
import types

import pytest

pytest.importorskip("homeassistant")

import conftest  # noqa: E402,F401

_spec = importlib.util.spec_from_file_location(
    "adaptive_light.config_flow",
    os.path.join(conftest.ROOT, "custom_components", "adaptive_light", "config_flow.py"),
)
cf = importlib.util.module_from_spec(_spec)
sys.modules["adaptive_light.config_flow"] = cf
_spec.loader.exec_module(cf)

from adaptive_light.const import CONF_CONFIG  # noqa: E402
from adaptive_light.manager import AdaptiveLightManager  # noqa: E402
from adaptive_light.model import Config  # noqa: E402

HASS = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))
FIELDS = ["name", "lamps", "presence", "off_timeout", "night", "night_start", "night_end", "night_still"]
ZONE_KEYS = {"key", "name", "lamps", "presence", "off_timeout", "night", "night_start", "night_end", "night_still"}


def run(coro):
    return asyncio.run(coro)


class _Entries:
    def async_update_entry(self, entry, title=None):
        entry.title = title


class Wizard(cf.AdaptiveLightConfigFlow):
    def __init__(self, entries=None):
        super().__init__()
        self.hass = HASS
        self._entries = list(entries or [])

    def _async_current_entries(self, include_ignore=None):
        return self._entries


class Options(cf.AdaptiveLightOptionsFlow):
    def __init__(self, cfg, title="Hall"):
        super().__init__()
        self._entry = types.SimpleNamespace(options={}, data={CONF_CONFIG: cfg}, title=title)
        self.hass = types.SimpleNamespace(config=HASS.config, config_entries=_Entries())

    @property
    def config_entry(self):
        return self._entry


def names(result):
    return [str(key) for key in result["data_schema"].schema]


def sub(flow, step, data=None):
    return run(getattr(flow, f"async_step_{step}")(data))


def _fill(name="Hall", lamps=None, presence=None, timeout=45):
    return {
        "name": name,
        "lamps": ["light.a", "light.b"] if lamps is None else lamps,
        "presence": ["binary_sensor.p"] if presence is None else presence,
        "off_timeout": timeout,
    }


def _entry(data, title):
    return types.SimpleNamespace(options={}, data=data, title=title)


def test_one_window_creates_one_zone_and_another_entry_is_independent():
    flow = Wizard()
    form = sub(flow, "user")
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == FIELDS
    created = sub(flow, "zone", _fill())
    assert created["type"] == "create_entry" and created["title"] == "Hall"
    assert len(created["data"]["config"]["zones"]) == 1
    zone = created["data"]["config"]["zones"][0]
    assert set(zone) == ZONE_KEYS
    assert zone["key"] == "hall" and zone["off_timeout"] == 45

    again = Wizard(entries=[_entry(created["data"], "Hall")])
    form = sub(again, "user")
    assert form["type"] == "form" and names(form) == FIELDS
    second = sub(again, "zone", _fill(name="Kitchen", lamps=["light.k"], presence=["binary_sensor.k"], timeout=20))
    assert second["type"] == "create_entry" and second["title"] == "Kitchen"
    other = second["data"]["config"]["zones"]
    assert len(other) == 1 and other[0]["key"] == "kitchen"
    assert other[0]["lamps"] == ["light.k"] and zone["lamps"] == ["light.a", "light.b"]

    same_name = Wizard(entries=[_entry(created["data"], "Hall")])
    third = sub(same_name, "zone", _fill(name="Hall", lamps=["light.c"], presence=["binary_sensor.c"]))
    assert third["title"] == "Hall"
    assert third["data"]["config"]["zones"][0]["key"] == "hall_2"
    assert third["data"]["config"]["zones"][0]["lamps"] == ["light.c"]


def test_options_is_the_same_window_and_keeps_this_zone_only():
    cfg = {
        "version": 5,
        "zones": [{
            "key": "hall", "name": "Hall", "lamps": ["light.a", "light.b"],
            "presence": ["binary_sensor.p"], "off_timeout": 45,
        }],
    }
    options = Options(cfg)
    menu = sub(options, "init")
    assert menu["type"] == "menu" and menu["menu_options"] == ["zone", "momentary"]
    form = sub(options, "zone")
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == FIELDS
    saved = sub(options, "zone", _fill(name="Hall renamed", lamps=["light.a"], presence=["binary_sensor.p", "binary_sensor.q"], timeout=10))
    assert saved["type"] == "create_entry"
    zones = saved["data"]["config"]["zones"]
    assert len(zones) == 1 and set(zones[0]) == ZONE_KEYS
    assert zones[0]["key"] == "hall" and zones[0]["name"] == "Hall renamed"
    assert zones[0]["lamps"] == ["light.a"] and zones[0]["off_timeout"] == 10
    assert options.config_entry.title == "Hall renamed"

    HASS.config.language = "ru"
    try:
        assert names(sub(Wizard(), "user")) == FIELDS
        assert names(sub(Options(cfg), "zone")) == FIELDS
    finally:
        HASS.config.language = "en"


class _States:
    def __init__(self):
        self._data = {}

    def get(self, entity):
        return self._data.get(entity)

    def set(self, entity, state):
        self._data[entity] = types.SimpleNamespace(state=state)


class _Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, dict(data)))


def test_the_flow_saves_the_four_fields_and_a_yaml_zone_still_loads():
    flow = Wizard()
    created = sub(flow, "zone", _fill(name="Hall", lamps=["light.a"], presence=["binary_sensor.p"], timeout=12))
    zone = created["data"]["config"]["zones"][0]
    assert zone["name"] == "Hall"
    assert zone["lamps"] == ["light.a"]
    assert zone["presence"] == ["binary_sensor.p"]
    assert zone["off_timeout"] == 12

    raw = {"version": 5, "zones": [{
        "key": "hall", "name": "Hall", "lamps": ["light.a"],
        "presence": ["binary_sensor.p"], "off_timeout": 12,
    }]}
    loaded = Config.from_dict(raw)
    assert loaded.zones["hall"]["lamps"] == ["light.a"]
    assert loaded.zones["hall"]["presence"] == ["binary_sensor.p"]
    assert loaded.zones["hall"]["off_timeout"] == 12
    assert loaded.zones["hall"]["night"] is False
    home = types.SimpleNamespace(states=_States(), services=_Services())
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.a", "off")
    mgr = AdaptiveLightManager(home, raw)
    assert run(mgr.apply("hall")) == "on"
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.a"]})]


def test_options_can_edit_a_momentary_action_and_saving_the_zone_keeps_it():
    cfg = {
        "version": 5,
        "zones": [{
            "key": "hall", "name": "Hall", "lamps": ["light.a"],
            "presence": ["binary_sensor.p"], "off_timeout": 45,
        }],
        "momentary_actions": [{
            "key": "fridge", "lamp": "light.kitchen", "color": "red", "duration": 5, "return": "restore",
            "trigger": {"type": "sensor", "entity_id": "binary_sensor.door"},
        }],
    }
    options = Options(cfg)
    form = sub(options, "momentary")
    assert form["type"] == "form" and names(form) == ["lamp", "color", "duration", "return"]
    saved = sub(options, "momentary", {"lamp": "light.kitchen", "color": "white", "duration": 8, "return": "off"})
    action = saved["data"]["config"]["momentary_actions"][0]
    assert action["lamp"] == "light.kitchen" and action["color"] == "white"
    assert action["duration"] == 8 and action["return"] == "off"
    assert action["trigger"] == {"type": "sensor", "entity_id": "binary_sensor.door"}
    assert saved["data"]["config"]["zones"][0]["key"] == "hall"

    again = Options(saved["data"]["config"])
    sub(again, "init")
    kept = sub(again, "zone", _fill(name="Hall", lamps=["light.a"], presence=["binary_sensor.p"], timeout=15))
    assert kept["data"]["config"]["zones"][0]["off_timeout"] == 15
    assert kept["data"]["config"]["momentary_actions"][0]["color"] == "white"
    assert kept["data"]["config"]["momentary_actions"][0]["trigger"]["entity_id"] == "binary_sensor.door"


def test_a_zone_without_lamps_or_presence_stays_on_the_form():
    flow = Wizard()
    result = sub(flow, "zone", _fill(lamps=[], presence=[]))
    assert result["step_id"] == "zone" and result["errors"]["lamps"] == "need_lamps"
    assert result["errors"]["presence"] == "need_presence"
    assert result["type"] == "form"

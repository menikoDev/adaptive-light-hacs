"""Each zone is its own entry. The four fields are the only window."""
import asyncio
import importlib.util
import json
import os
import sys
import types

import pytest
import voluptuous as vol

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
FIELDS = ["name", "lamps", "presence", "lux", "threshold", "window", "priority", "off_timeout", "rgb", "color_temperature", "brightness_level", "active_hours", "night_mode"]
ZONE_KEYS = {"key", "name", "lamps", "presence", "lux", "threshold", "window", "priority", "color", "color_temp", "brightness", "hold_color", "hold_brightness", "lux_coefficient", "manual_clear", "off_timeout", "night", "night_start", "night_end", "night_still", "active_start", "active_end"}


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


def _section_fields(group):
    return {str(key): (key, selector) for key, selector in group.schema.schema.items()}


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
    menu = sub(flow, "user")
    assert menu["type"] == "menu" and menu["menu_options"] == ["zone", "outdoor"]
    form = sub(flow, "zone")
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == FIELDS
    assert "lux_coefficient" not in names(form)
    created = sub(flow, "zone", _fill())
    assert created["type"] == "create_entry" and created["title"] == "Hall"
    assert len(created["data"]["config"]["zones"]) == 1
    zone = created["data"]["config"]["zones"][0]
    assert set(zone) == ZONE_KEYS
    assert zone["key"] == "hall" and zone["off_timeout"] == 45

    again = Wizard(entries=[_entry(created["data"], "Hall")])
    form = sub(again, "zone")
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
    form = sub(options, "init")
    assert form["type"] == "form" and form["step_id"] == "zone" and "menu_options" not in form
    assert names(form) == FIELDS
    saved = sub(options, "zone", _fill(name="Hall renamed", lamps=["light.a"], presence=["binary_sensor.p", "binary_sensor.q"], timeout=10))
    assert saved["type"] == "create_entry"
    zones = saved["data"]["config"]["zones"]
    assert len(zones) == 1 and set(zones[0]) == ZONE_KEYS
    assert zones[0]["key"] == "hall" and zones[0]["name"] == "Hall renamed"
    assert zones[0]["manual_clear"] == 1
    assert zones[0]["lamps"] == ["light.a"] and zones[0]["off_timeout"] == 10
    assert options.config_entry.title == "Hall renamed"

    HASS.config.language = "ru"
    try:
        assert names(sub(Wizard(), "zone")) == FIELDS
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


def _schema_fields(result):
    return {str(key): (key, selector) for key, selector in result["data_schema"].schema.items()}


def test_zone_form_uses_a_color_picker_and_a_brightness_slider_and_russian_labels():
    cfg = {
        "version": 5,
        "zones": [{
            "key": "hall", "name": "Hall", "lamps": ["light.a"],
            "presence": ["binary_sensor.p"], "off_timeout": 45,
        }],
    }
    create = _schema_fields(sub(Wizard(), "zone"))
    options = _schema_fields(sub(Options(cfg), "zone"))
    for fields in (create, options):
        assert "color" not in fields
        rgb = _section_fields(fields["rgb"][1])
        for channel in ("color_r", "color_g", "color_b"):
            channel_key, channel_sel = rgb[channel]
            assert type(channel_key) is vol.Optional
            assert channel_sel.selector_type == "number" and channel_sel.config["mode"] == "box"
            assert channel_sel.config["min"] == 0 and channel_sel.config["max"] == 255
        kelvin = _section_fields(fields["color_temperature"][1])
        bright = _section_fields(fields["brightness_level"][1])
        kelvin_key, kelvin_sel = kelvin["color_temp"]
        bright_key, bright_sel = bright["brightness"]
        assert type(kelvin_key) is vol.Optional and type(bright_key) is vol.Optional
        assert kelvin_sel.selector_type == "color_temp" and kelvin_sel.config["unit"] == "kelvin"
        assert kelvin_sel.config["min"] == 1000 and kelvin_sel.config["max"] == 10000
        assert bright_sel.selector_type == "number" and bright_sel.config["mode"] == "slider"
        assert not (kelvin_key.description or {}).get("suggested_value")
        assert (bright_key.description or {}).get("suggested_value") in (None, "")
        assert type(kelvin["hold_color"][0]) is vol.Required
        assert type(bright["hold_brightness"][0]) is vol.Required
        assert type(fields["priority"][0]) is vol.Required
        assert "lux_gap" not in fields
        assert fields["window"][1].selector_type == "number"
        assert fields["window"][1].config["unit_of_measurement"] == "min"
        assert (fields["window"][0].description or {}).get("suggested_value") == 10
        assert fields["off_timeout"][1].selector_type == "number"
        night = _section_fields(fields["night_mode"][1])
        assert night["night"][1].selector_type == "boolean"
        hours = _section_fields(fields["active_hours"][1])
        assert hours["active_start"][1].selector_type == "time"
        assert hours["active_end"][1].selector_type == "time"

    opened = sub(Options(cfg), "init")
    assert opened["type"] == "form" and opened["step_id"] == "zone" and "menu_options" not in opened
    assert names(opened) == FIELDS

    root = os.path.join(conftest.ROOT, "custom_components", "adaptive_light")
    strings = json.loads(open(os.path.join(root, "strings.json"), encoding="utf-8").read())
    en = json.loads(open(os.path.join(root, "translations", "en.json"), encoding="utf-8").read())
    ru = json.loads(open(os.path.join(root, "translations", "ru.json"), encoding="utf-8").read())
    assert strings == en
    assert "The zone, or a momentary action on this entry." not in json.dumps(strings)
    assert "init" not in strings["options"]["step"] and "init" not in strings["config"]["step"]
    paths = [
        ("options", "step", "zone", "title"),
        ("options", "step", "zone", "data", "priority"),
        ("options", "step", "zone", "data", "color_r"),
        ("options", "step", "zone", "data", "color_g"),
        ("options", "step", "zone", "data", "color_b"),
        ("options", "step", "zone", "data", "color_temp"),
        ("options", "step", "zone", "data", "brightness"),
        ("options", "step", "zone", "data", "hold_color"),
        ("options", "step", "zone", "data", "hold_brightness"),
        ("config", "step", "user", "menu_options", "zone"),
        ("config", "step", "user", "menu_options", "outdoor"),
        ("config", "step", "zone", "data", "priority"),
        ("config", "step", "zone", "data", "color_r"),
        ("config", "step", "zone", "data", "color_g"),
        ("config", "step", "zone", "data", "color_b"),
        ("config", "step", "zone", "data", "color_temp"),
        ("config", "step", "zone", "data", "brightness"),
        ("config", "step", "zone", "data", "hold_color"),
        ("config", "step", "zone", "data", "hold_brightness"),
    ]
    russian = {}
    for path in paths:
        english = en["config" if path[0] == "config" else "options"]
        translated = ru["config" if path[0] == "config" else "options"]
        for part in path[1:]:
            english = english[part]
            translated = translated[part]
        assert translated != english and translated
        russian[".".join(path)] = translated
    assert russian["options.step.zone.title"] == "Зона"
    assert russian["options.step.zone.data.priority"] == "Приоритет"
    assert russian["options.step.zone.data.color_r"] == "Красный"
    assert russian["options.step.zone.data.color_g"] == "Зелёный"
    assert russian["options.step.zone.data.color_b"] == "Синий"
    assert en["options"]["step"]["zone"]["data"]["hold_color"] == "Hold color"
    assert en["options"]["step"]["zone"]["data"]["hold_brightness"] == "Hold brightness"
    assert en["options"]["step"]["zone"]["data"]["color_r"] == "Red"
    assert russian["options.step.zone.data.color_temp"] == "Цветовая температура, К"
    assert russian["options.step.zone.data.brightness"] == "Яркость"
    assert russian["options.step.zone.data.hold_color"] == "Удерживать цвет"
    assert russian["options.step.zone.data.hold_brightness"] == "Удерживать яркость"
    assert russian["config.step.user.menu_options.zone"] == "Зона"
    assert russian["config.step.user.menu_options.outdoor"] == "Уличный свет"

    saved = sub(Wizard(), "zone", _fill())
    zone = saved["data"]["config"]["zones"][0]
    assert zone["color"] == "" and zone["color_temp"] is None and zone["brightness"] is None
    assert zone["hold_color"] is False and zone["hold_brightness"] is False
    picked = sub(Wizard(), "zone", {
        **_fill(name="Kitchen", lamps=["light.k"], presence=["binary_sensor.k"]),
        "color_r": 9, "color_g": 8, "color_b": 7, "brightness": 40, "hold_color": True, "hold_brightness": True,
    })
    chosen = picked["data"]["config"]["zones"][0]
    assert chosen["color"] == [9, 8, 7] and chosen["brightness"] == 40
    assert chosen["hold_color"] is True and chosen["hold_brightness"] is True
    home = types.SimpleNamespace(states=_States(), services=_Services())
    home.states.set("binary_sensor.k", "on")
    home.states.set("light.k", "off")
    mgr = AdaptiveLightManager(home, picked["data"]["config"])
    assert run(mgr.apply(chosen["key"])) == "on"
    assert home.services.calls == [
        ("light", "turn_on", {"entity_id": ["light.k"], "rgb_color": [9, 8, 7], "brightness": 40}),
        ("light", "turn_on", {"entity_id": ["light.k"], "rgb_color": [9, 8, 7]}),
    ]


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


def test_outdoor_entry_is_one_controller_and_a_saved_coefficient_stays_hidden():
    flow = Wizard()
    form = sub(flow, "outdoor")
    assert form["type"] == "form" and names(form) == ["radiation", "factor"]
    created = sub(flow, "outdoor", {"radiation": "sensor.rad", "factor": 0.6})
    assert created["type"] == "create_entry" and created["title"] == "Outdoor"
    outdoor = created["data"]["config"]["outdoor"]
    assert created["data"]["config"]["zones"] == []
    assert outdoor == {"radiation": "sensor.rad", "factor": 0.6}
    again = Wizard(entries=[_entry(created["data"], "Outdoor")])
    aborted = sub(again, "outdoor")
    assert aborted["type"] == "abort" and aborted["reason"] == "already_configured"

    cfg = {
        "version": 5,
        "zones": [{
            "key": "hall", "name": "Hall", "lamps": ["light.a"],
            "presence": ["binary_sensor.p"], "off_timeout": 45, "lux_coefficient": 2,
        }],
    }
    options = Options(cfg)
    sub(options, "init")
    assert "lux_coefficient" not in names(sub(options, "zone"))
    saved = sub(options, "zone", _fill(name="Hall", lamps=["light.a"], presence=["binary_sensor.p"], timeout=45))
    assert saved["data"]["config"]["zones"][0]["lux_coefficient"] == 2

    outdoor_options = Options(created["data"]["config"], title="Outdoor")
    opened = sub(outdoor_options, "init")
    assert opened["type"] == "form" and opened["step_id"] == "outdoor"


def test_a_zone_without_lamps_or_presence_stays_on_the_form():
    flow = Wizard()
    result = sub(flow, "zone", _fill(lamps=[], presence=[]))
    assert result["step_id"] == "zone" and result["errors"]["lamps"] == "need_lamps"
    assert result["errors"]["presence"] == "need_presence"
    assert result["type"] == "form"

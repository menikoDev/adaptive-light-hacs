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

HASS = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))
EN = ["Zone name", "Lamps", "Presence sensors", "Off timeout, s", "Night mode", "Night from", "Night until", "Still, min"]
RU = ["Название зоны", "Лампы", "Датчики присутствия", "Таймаут выключения, с", "Ночной режим", "Ночь с", "Ночь до", "Без движения, мин"]
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


def _fill(labels, name="Hall", lamps=None, presence=None, timeout=45):
    return {
        labels[0]: name,
        labels[1]: lamps if lamps is not None else ["light.a", "light.b"],
        labels[2]: presence if presence is not None else ["binary_sensor.p"],
        labels[3]: timeout,
    }


def _entry(data, title):
    return types.SimpleNamespace(options={}, data=data, title=title)


def test_one_window_creates_one_zone_and_another_entry_is_independent():
    flow = Wizard()
    form = sub(flow, "user")
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == EN
    created = sub(flow, "zone", _fill(EN))
    assert created["type"] == "create_entry" and created["title"] == "Hall"
    assert len(created["data"]["config"]["zones"]) == 1
    zone = created["data"]["config"]["zones"][0]
    assert set(zone) == ZONE_KEYS
    assert zone["key"] == "hall" and zone["off_timeout"] == 45

    again = Wizard(entries=[_entry(created["data"], "Hall")])
    form = sub(again, "user")
    assert form["type"] == "form" and names(form) == EN
    second = sub(again, "zone", _fill(EN, name="Kitchen", lamps=["light.k"], presence=["binary_sensor.k"], timeout=20))
    assert second["type"] == "create_entry" and second["title"] == "Kitchen"
    other = second["data"]["config"]["zones"]
    assert len(other) == 1 and other[0]["key"] == "kitchen"
    assert other[0]["lamps"] == ["light.k"] and zone["lamps"] == ["light.a", "light.b"]

    same_name = Wizard(entries=[_entry(created["data"], "Hall")])
    third = sub(same_name, "zone", _fill(EN, name="Hall", lamps=["light.c"], presence=["binary_sensor.c"]))
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
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == EN
    saved = sub(options, "zone", _fill(EN, name="Hall renamed", lamps=["light.a"], presence=["binary_sensor.p", "binary_sensor.q"], timeout=10))
    assert saved["type"] == "create_entry"
    zones = saved["data"]["config"]["zones"]
    assert len(zones) == 1 and set(zones[0]) == ZONE_KEYS
    assert zones[0]["key"] == "hall" and zones[0]["name"] == "Hall renamed"
    assert zones[0]["lamps"] == ["light.a"] and zones[0]["off_timeout"] == 10
    assert options.config_entry.title == "Hall renamed"

    HASS.config.language = "ru"
    try:
        russian = Wizard()
        assert names(sub(russian, "user")) == RU
        ru_options = Options(cfg)
        assert names(sub(ru_options, "init")) == RU
    finally:
        HASS.config.language = "en"


def test_a_zone_without_lamps_or_presence_stays_on_the_form():
    flow = Wizard()
    result = sub(flow, "zone", _fill(EN, lamps=[], presence=[]))
    assert result["step_id"] == "zone" and result["errors"]["Lamps"] == "need_lamps"
    assert result["errors"]["Presence sensors"] == "need_presence"
    assert result["type"] == "form"

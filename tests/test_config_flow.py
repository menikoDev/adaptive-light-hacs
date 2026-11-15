"""The wizard and the options flow edit a zone through the same four fields."""
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
EN = ["Zone name", "Lamps", "Presence sensors", "Off timeout, s"]
RU = ["Название зоны", "Лампы", "Датчики присутствия", "Таймаут выключения, с"]
ZONE_KEYS = {"key", "name", "lamps", "presence", "off_timeout"}


def run(coro):
    return asyncio.run(coro)


class Wizard(cf.AdaptiveLightConfigFlow):
    def __init__(self):
        super().__init__()
        self.hass = HASS

    def _async_current_entries(self, include_ignore=None):
        return []


class Options(cf.AdaptiveLightOptionsFlow):
    def __init__(self, cfg):
        super().__init__()
        self.hass = HASS
        self._entry = types.SimpleNamespace(options={}, data={CONF_CONFIG: cfg})

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


def test_the_wizard_and_options_edit_the_same_four_fields():
    flow = Wizard()
    form = sub(flow, "user")
    assert form["type"] == "form" and form["step_id"] == "zone"
    assert names(form) == EN
    menu = sub(flow, "zone", _fill(EN))
    assert menu["type"] == "menu"
    assert menu["menu_options"] == {"zone": "Add another zone", "save": "Finish"}
    zone = flow.cfg["zones"][0]
    assert set(zone) == ZONE_KEYS
    assert zone == {
        "key": "hall", "name": "Hall", "lamps": ["light.a", "light.b"],
        "presence": ["binary_sensor.p"], "off_timeout": 45,
    }
    created = sub(flow, "save")
    assert created["type"] == "create_entry"
    assert set(created["data"]["config"]) == {"version", "zones"}
    assert "rules" not in created["data"]["config"]

    options = Options(created["data"]["config"])
    menu = sub(options, "init")
    assert menu["menu_options"] == {"pick": "Edit a zone", "add": "Add a zone"}
    picked = sub(options, "pick")
    assert names(picked) == ["Zone"]
    sub(options, "pick", {"Zone": "hall"})
    assert sub(options, "zone_menu")["menu_options"]["edit"] == "Edit"
    edited = sub(options, "edit")
    assert names(edited) == EN
    saved = sub(options, "edit", _fill(EN, lamps=["light.a"], presence=["binary_sensor.p", "binary_sensor.q"], timeout=10))
    assert saved["type"] == "create_entry"
    zone = saved["data"]["config"]["zones"][0]
    assert set(zone) == ZONE_KEYS
    assert zone["key"] == "hall" and zone["lamps"] == ["light.a"]
    assert zone["presence"] == ["binary_sensor.p", "binary_sensor.q"] and zone["off_timeout"] == 10

    HASS.config.language = "ru"
    try:
        russian = Wizard()
        assert names(sub(russian, "user")) == RU
        assert sub(russian, "zone", _fill(RU, name="Кухня"))["menu_options"]["save"] == "Готово"
        again = Options(created["data"]["config"])
        assert sub(again, "init")["menu_options"]["add"] == "Добавить зону"
        assert names(sub(again, "edit")) == RU
    finally:
        HASS.config.language = "en"


def test_a_zone_without_lamps_or_presence_stays_on_the_form():
    flow = Wizard()
    result = sub(flow, "zone", _fill(EN, lamps=[], presence=[]))
    assert result["step_id"] == "zone" and result["errors"]["Lamps"] == "need_lamps"
    assert result["errors"]["Presence sensors"] == "need_presence"
    assert flow.cfg["zones"] == []

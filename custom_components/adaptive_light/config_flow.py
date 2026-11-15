"""Wizard and options: one zone form with four fields."""
from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from homeassistant.util import slugify

from .const import CONF_CONFIG, DOMAIN
from .model import empty_config, normalise, unique_key, upsert_zone, validate

LABELS = {
    "en": {
        "name": "Zone name",
        "lamps": "Lamps",
        "presence": "Presence sensors",
        "off_timeout": "Off timeout, s",
        "pick": "Zone",
    },
    "ru": {
        "name": "Название зоны",
        "lamps": "Лампы",
        "presence": "Датчики присутствия",
        "off_timeout": "Таймаут выключения, с",
        "pick": "Зона",
    },
}
_TEXT = {
    "add_another": ("Add another zone", "Ещё зона"),
    "finish": ("Finish", "Готово"),
    "edit_zone": ("Edit a zone", "Изменить зону"),
    "add_zone": ("Add a zone", "Добавить зону"),
    "edit": ("Edit", "Изменить"),
    "remove": ("Remove zone", "Удалить зону"),
    "back": ("Back", "Назад"),
}


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
    return out


def _timeout(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    number = int(value)
    if number < 0:
        return None
    return number


class _Fields:
    cfg: dict
    _zone: str = ""

    def _ru(self) -> bool:
        lang = getattr(getattr(self, "hass", None), "config", None)
        return str(getattr(lang, "language", "en") or "en").lower().startswith("ru")

    def _labels(self) -> dict[str, str]:
        return LABELS["ru" if self._ru() else "en"]

    def _t(self, key: str) -> str:
        en, ru = _TEXT[key]
        return ru if self._ru() else en

    def _zone_name(self) -> str:
        for zone in self.cfg["zones"]:
            if zone["key"] == self._zone:
                return zone["name"]
        return self._zone

    def _current(self, submitted: dict | None) -> dict:
        labels = self._labels()
        if submitted is not None:
            rev = {label: key for key, label in labels.items()}
            return {rev.get(key, key): value for key, value in submitted.items()}
        for zone in self.cfg["zones"]:
            if zone["key"] == self._zone:
                return zone
        return {"off_timeout": 60}

    def _field(self, key: str, selector, value: Any):
        label = self._labels()[key]
        if value in (None, "", []):
            return vol.Required(label), selector
        return vol.Required(label, description={"suggested_value": value}), selector

    def _schema(self, current: dict) -> vol.Schema:
        name, name_sel = self._field("name", sel.TextSelector(), current.get("name") or None)
        lamps, lamp_sel = self._field(
            "lamps", sel.EntitySelector(sel.EntitySelectorConfig(domain="light", multiple=True)), current.get("lamps") or []
        )
        presence, presence_sel = self._field(
            "presence",
            sel.EntitySelector(sel.EntitySelectorConfig(domain="binary_sensor", multiple=True)),
            current.get("presence") or [],
        )
        timeout, timeout_sel = self._field(
            "off_timeout",
            sel.NumberSelector(sel.NumberSelectorConfig(min=0, max=604800, step=1, unit_of_measurement="s", mode=sel.NumberSelectorMode.BOX)),
            current.get("off_timeout", 60),
        )
        return vol.Schema({name: name_sel, lamps: lamp_sel, presence: presence_sel, timeout: timeout_sel})

    def _parse(self, user_input: dict) -> tuple[dict | None, dict]:
        labels = self._labels()
        name = str(user_input.get(labels["name"]) or "").strip()
        lamps = _as_list(user_input.get(labels["lamps"]))
        presence = _as_list(user_input.get(labels["presence"]))
        timeout = _timeout(user_input.get(labels["off_timeout"]))
        errors: dict[str, str] = {}
        if not name:
            errors[labels["name"]] = "need_name"
        if not lamps:
            errors[labels["lamps"]] = "need_lamps"
        elif any(not item.startswith("light.") for item in lamps):
            errors[labels["lamps"]] = "bad_lamp"
        if not presence:
            errors[labels["presence"]] = "need_presence"
        elif any(not item.startswith("binary_sensor.") for item in presence):
            errors[labels["presence"]] = "bad_presence"
        if timeout is None:
            errors[labels["off_timeout"]] = "bad_timeout"
        if errors:
            return None, errors
        return {"name": name, "lamps": lamps, "presence": presence, "off_timeout": timeout}, {}

    def _store(self, parsed: dict) -> None:
        used = {zone["key"] for zone in self.cfg["zones"] if zone["key"] != self._zone}
        key = self._zone or unique_key(slugify(parsed["name"]) or "zone", used)
        upsert_zone(self.cfg, {"key": key, **parsed})
        self._zone = ""

    def _form(self, current: dict, errors: dict | None):
        return self.async_show_form(step_id="zone", data_schema=self._schema(current), errors=errors or None)

    async def async_step_zone(self, user_input=None):
        if user_input is not None:
            parsed, errors = self._parse(user_input)
            if errors:
                return self._form(self._current(user_input), errors)
            self._store(parsed)
            if self._saving:
                return self._commit()
            return await self.async_step_more()
        return self._form(self._current(None), None)

    async def async_step_more(self, user_input=None):
        return self.async_show_menu(
            step_id="more",
            menu_options={"zone": self._t("add_another"), "save": self._t("finish")},
        )

    async def async_step_save(self, user_input=None):
        cfg = normalise(self.cfg)
        errors = validate(cfg)
        if errors:
            return self.async_show_form(step_id="save", data_schema=vol.Schema({}), errors={"base": errors[0].code})
        return self.async_create_entry(title="Adaptive Light", data={CONF_CONFIG: cfg})

    def _commit(self):
        cfg = normalise(self.cfg)
        errors = validate(cfg)
        if errors:
            return self.async_show_form(step_id="zone", data_schema=vol.Schema({}), errors={"base": errors[0].code})
        return self.async_create_entry(title="", data={CONF_CONFIG: cfg})


class AdaptiveLightConfigFlow(_Fields, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3
    _saving = False

    def __init__(self) -> None:
        self.cfg = empty_config()
        self._zone = ""

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self.async_step_zone(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return AdaptiveLightOptionsFlow()


class AdaptiveLightOptionsFlow(_Fields, config_entries.OptionsFlow):
    _saving = True

    def __init__(self) -> None:
        self._loaded = False
        self._zone = ""

    def _load(self) -> None:
        if self._loaded:
            return
        raw = self.config_entry.options.get(CONF_CONFIG) or self.config_entry.data[CONF_CONFIG]
        self.cfg = copy.deepcopy(raw)
        self._loaded = True

    async def async_step_init(self, user_input=None):
        self._load()
        options = {}
        if self.cfg.get("zones"):
            options["pick"] = self._t("edit_zone")
        options["add"] = self._t("add_zone")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_add(self, user_input=None):
        self._load()
        self._zone = ""
        return await self.async_step_zone(user_input)

    async def async_step_pick(self, user_input=None):
        self._load()
        label = self._labels()["pick"]
        if user_input is not None:
            self._zone = user_input[label]
            return await self.async_step_zone_menu()
        options = [{"value": zone["key"], "label": zone["name"]} for zone in self.cfg["zones"]]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema({vol.Required(label): sel.SelectSelector(sel.SelectSelectorConfig(options=options))}),
        )

    async def async_step_zone_menu(self, user_input=None):
        self._load()
        return self.async_show_menu(
            step_id="zone_menu",
            menu_options={"edit": self._t("edit"), "remove": self._t("remove"), "init": self._t("back")},
            description_placeholders={"zone": self._zone_name()},
        )

    async def async_step_edit(self, user_input=None):
        self._load()
        return await self.async_step_zone(user_input)

    async def async_step_remove(self, user_input=None):
        self._load()
        if user_input is not None:
            self.cfg["zones"] = [zone for zone in self.cfg["zones"] if zone["key"] != self._zone]
            self._zone = ""
            return self.async_create_entry(title="", data={CONF_CONFIG: normalise(self.cfg)})
        return self.async_show_form(
            step_id="remove",
            data_schema=vol.Schema({}),
            description_placeholders={"zone": self._zone_name()},
        )

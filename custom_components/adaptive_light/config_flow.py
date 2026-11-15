"""One window, one zone. Another zone is another config entry."""
from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from homeassistant.util import slugify

from .const import CONF_CONFIG, DOMAIN
from .model import empty_config, normalise, unique_key, validate

LABELS = {
    "en": {
        "name": "Zone name",
        "lamps": "Lamps",
        "presence": "Presence sensors",
        "off_timeout": "Off timeout, s",
        "night": "Night mode",
        "night_start": "Night from",
        "night_end": "Night until",
        "night_still": "Still, min",
    },
    "ru": {
        "name": "Название зоны",
        "lamps": "Лампы",
        "presence": "Датчики присутствия",
        "off_timeout": "Таймаут выключения, с",
        "night": "Ночной режим",
        "night_start": "Ночь с",
        "night_end": "Ночь до",
        "night_still": "Без движения, мин",
    },
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


def _hhmm(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


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
    """The four fields, shared by the create flow and the options flow."""

    cfg: dict
    _zone: str = ""

    def _ru(self) -> bool:
        lang = getattr(getattr(self, "hass", None), "config", None)
        return str(getattr(lang, "language", "en") or "en").lower().startswith("ru")

    def _labels(self) -> dict[str, str]:
        return LABELS["ru" if self._ru() else "en"]

    def _used_keys(self) -> set[str]:
        used: set[str] = set()
        getter = getattr(self, "_async_current_entries", None)
        entries = getter() if getter else []
        for entry in entries or []:
            raw = {}
            options = getattr(entry, "options", None) or {}
            data = getattr(entry, "data", None) or {}
            if isinstance(options, dict):
                raw = options.get(CONF_CONFIG) or raw
            if not raw and isinstance(data, dict):
                raw = data.get(CONF_CONFIG) or {}
            for zone in (raw.get("zones") or []) if isinstance(raw, dict) else []:
                key = zone.get("key") if isinstance(zone, dict) else None
                if isinstance(key, str) and key != self._zone:
                    used.add(key)
        return used

    def _current(self, submitted: dict | None) -> dict:
        labels = self._labels()
        if submitted is not None:
            rev = {label: key for key, label in labels.items()}
            return {rev.get(key, key): value for key, value in submitted.items()}
        for zone in self.cfg.get("zones") or []:
            if zone.get("key") == self._zone:
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
            sel.NumberSelector(sel.NumberSelectorConfig(
                min=0, max=604800, step=1, unit_of_measurement="s", mode=sel.NumberSelectorMode.BOX,
            )),
            current.get("off_timeout", 60),
        )
        night, night_sel = self._field("night", sel.BooleanSelector(), current.get("night") is True)
        start, start_sel = self._field("night_start", sel.TimeSelector(), current.get("night_start") or "02:00")
        end, end_sel = self._field("night_end", sel.TimeSelector(), current.get("night_end") or "06:00")
        still, still_sel = self._field(
            "night_still",
            sel.NumberSelector(sel.NumberSelectorConfig(min=1, max=720, step=1, unit_of_measurement="min", mode=sel.NumberSelectorMode.BOX)),
            current.get("night_still", 60),
        )
        return vol.Schema({
            name: name_sel, lamps: lamp_sel, presence: presence_sel, timeout: timeout_sel,
            night: night_sel, start: start_sel, end: end_sel, still: still_sel,
        })

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
        night = user_input.get(labels["night"]) is True
        start = _hhmm(user_input.get(labels["night_start"])) or "02:00"
        end = _hhmm(user_input.get(labels["night_end"])) or "06:00"
        still = _timeout(user_input.get(labels["night_still"]))
        if still is None:
            still = 60
        if still < 1:
            errors[labels["night_still"]] = "bad_timeout"
        if errors:
            return None, errors
        return {
            "name": name, "lamps": lamps, "presence": presence, "off_timeout": timeout,
            "night": night, "night_start": start, "night_end": end, "night_still": still,
        }, {}

    def _store(self, parsed: dict) -> str:
        if not self._zone:
            self._zone = unique_key(slugify(parsed["name"]) or "zone", self._used_keys())
        self.cfg = empty_config()
        self.cfg["zones"] = [{"key": self._zone, **parsed}]
        return parsed["name"]

    def _form(self, current: dict, errors: dict | None):
        return self.async_show_form(step_id="zone", data_schema=self._schema(current), errors=errors or None)

    def _finish(self, title: str):
        cfg = normalise(self.cfg)
        errors = validate(cfg)
        if errors:
            return self._form(self._current(None), {"base": errors[0].code})
        if self._saving:
            entries = getattr(self.hass, "config_entries", None)
            update = getattr(entries, "async_update_entry", None)
            if update is not None and getattr(self, "config_entry", None) is not None:
                update(self.config_entry, title=title)
            return self.async_create_entry(title="", data={CONF_CONFIG: cfg})
        return self.async_create_entry(title=title, data={CONF_CONFIG: cfg})

    async def async_step_zone(self, user_input=None):
        if user_input is not None:
            parsed, errors = self._parse(user_input)
            if errors:
                return self._form(self._current(user_input), errors)
            title = self._store(parsed)
            return self._finish(title)
        return self._form(self._current(None), None)


class AdaptiveLightConfigFlow(_Fields, config_entries.ConfigFlow, domain=DOMAIN):
    """Create one zone. A second zone is a second entry, not a second step."""

    VERSION = 3
    _saving = False

    def __init__(self) -> None:
        self.cfg = empty_config()
        self._zone = ""

    async def async_step_user(self, user_input=None):
        return await self.async_step_zone(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return AdaptiveLightOptionsFlow()


class AdaptiveLightOptionsFlow(_Fields, config_entries.OptionsFlow):
    """The same four fields, for this entry only."""

    _saving = True

    def __init__(self) -> None:
        self._loaded = False
        self._zone = ""
        self.cfg = empty_config()

    def _load(self) -> None:
        if self._loaded:
            return
        raw = self.config_entry.options.get(CONF_CONFIG) or self.config_entry.data.get(CONF_CONFIG) or {}
        self.cfg = copy.deepcopy(raw) if isinstance(raw, dict) else empty_config()
        zones = [zone for zone in self.cfg.get("zones") or [] if isinstance(zone, dict) and zone.get("key")]
        self._zone = zones[0]["key"] if zones else ""
        self._loaded = True

    async def async_step_init(self, user_input=None):
        self._load()
        return await self.async_step_zone(user_input)

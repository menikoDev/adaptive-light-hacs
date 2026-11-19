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
from .model import empty_config, normalise, outdoor_block, unique_key, validate
from .momentary import keep_section, parse_actions, write_action

# Schema keys are the translation keys. The visible words live in strings.json
# and translations/{en,ru}.json under the same key.


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


def _priority_input(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not value.is_integer():
        return 0
    number = int(value)
    return number if number > 0 else 0


def _brightness_input(value: Any) -> tuple[int | None, bool]:
    if value in (None, ""):
        return None, False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, True
    if isinstance(value, float) and not value.is_integer():
        return None, True
    number = int(value)
    if number < 0 or number > 255:
        return None, True
    return number, False


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
        if submitted is not None:
            return submitted
        for zone in self.cfg.get("zones") or []:
            if zone.get("key") == self._zone:
                return zone
        return {"off_timeout": 60}

    def _field(self, key: str, selector, value: Any):
        if value in (None, "", []):
            return vol.Required(key), selector
        return vol.Required(key, description={"suggested_value": value}), selector

    def _optional(self, key: str, selector, value: Any):
        if value in (None, "", []):
            return vol.Optional(key), selector
        return vol.Optional(key, description={"suggested_value": value}), selector

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
        lux, lux_sel = self._optional(
            "lux",
            sel.EntitySelector(sel.EntitySelectorConfig(domain="sensor", multiple=False)),
            current.get("lux") or None,
        )
        threshold, threshold_sel = self._field(
            "threshold",
            sel.NumberSelector(sel.NumberSelectorConfig(
                min=0, max=100000, step=1, unit_of_measurement="lx", mode=sel.NumberSelectorMode.BOX,
            )),
            current.get("threshold", 50),
        )
        gap, gap_sel = self._field(
            "lux_gap",
            sel.NumberSelector(sel.NumberSelectorConfig(
                min=0, max=100000, step=1, unit_of_measurement="lx", mode=sel.NumberSelectorMode.BOX,
            )),
            current.get("lux_gap", 0),
        )
        priority, priority_sel = self._field(
            "priority",
            sel.NumberSelector(sel.NumberSelectorConfig(
                min=0, max=1000, step=1, mode=sel.NumberSelectorMode.BOX,
            )),
            current.get("priority", 0),
        )
        color, color_sel = self._optional("color", sel.TextSelector(), current.get("color") or None)
        brightness, brightness_sel = self._optional(
            "brightness",
            sel.NumberSelector(sel.NumberSelectorConfig(
                min=0, max=255, step=1, mode=sel.NumberSelectorMode.BOX,
            )),
            current.get("brightness"),
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
            name: name_sel, lamps: lamp_sel, presence: presence_sel, lux: lux_sel, threshold: threshold_sel, gap: gap_sel,
            priority: priority_sel, color: color_sel, brightness: brightness_sel,
            timeout: timeout_sel, night: night_sel, start: start_sel, end: end_sel, still: still_sel,
        })

    def _parse(self, user_input: dict) -> tuple[dict | None, dict]:
        name = str(user_input.get("name") or "").strip()
        lamps = _as_list(user_input.get("lamps"))
        presence = _as_list(user_input.get("presence"))
        timeout = _timeout(user_input.get("off_timeout"))
        errors: dict[str, str] = {}
        if not name:
            errors["name"] = "need_name"
        if not lamps:
            errors["lamps"] = "need_lamps"
        elif any(not item.startswith("light.") for item in lamps):
            errors["lamps"] = "bad_lamp"
        if not presence:
            errors["presence"] = "need_presence"
        elif any(not item.startswith("binary_sensor.") for item in presence):
            errors["presence"] = "bad_presence"
        if timeout is None:
            errors["off_timeout"] = "bad_timeout"
        lux = user_input.get("lux") or ""
        if isinstance(lux, list):
            lux = lux[0] if lux else ""
        if not isinstance(lux, str):
            lux = ""
        if lux and not lux.startswith("sensor."):
            errors["lux"] = "bad_lux"
        if "threshold" not in user_input or user_input.get("threshold") in (None, ""):
            threshold = 50
        else:
            threshold = _timeout(user_input.get("threshold"))
            if threshold is None:
                errors["threshold"] = "bad_threshold"
        if "lux_gap" not in user_input or user_input.get("lux_gap") in (None, ""):
            gap = 0
        else:
            gap = _timeout(user_input.get("lux_gap"))
            if gap is None:
                errors["lux_gap"] = "bad_gap"
        priority = _priority_input(user_input.get("priority") if "priority" in user_input else None)
        color = str(user_input.get("color") or "").strip()
        brightness, brightness_bad = _brightness_input(user_input.get("brightness") if "brightness" in user_input else None)
        if brightness_bad:
            errors["brightness"] = "bad_brightness"
        night = user_input.get("night") is True
        start = _hhmm(user_input.get("night_start")) or "02:00"
        end = _hhmm(user_input.get("night_end")) or "06:00"
        still = _timeout(user_input.get("night_still"))
        if still is None:
            still = 60
        if still < 1:
            errors["night_still"] = "bad_timeout"
        if errors:
            return None, errors
        coefficient = 1
        for zone in self.cfg.get("zones") or []:
            if isinstance(zone, dict) and zone.get("key") == self._zone and zone.get("lux_coefficient") not in (None, ""):
                coefficient = zone["lux_coefficient"]
        return {
            "name": name, "lamps": lamps, "presence": presence, "off_timeout": timeout,
            "lux": lux, "threshold": threshold, "lux_gap": gap, "priority": priority,
            "color": color, "brightness": brightness, "lux_coefficient": coefficient,
            "night": night, "night_start": start, "night_end": end, "night_still": still,
        }, {}

    def _store(self, parsed: dict) -> str:
        actions = self.cfg.get("momentary_actions") if isinstance(self.cfg, dict) else None
        if not self._zone:
            self._zone = unique_key(slugify(parsed["name"]) or "zone", self._used_keys())
        previous_clear = 0
        for zone in self.cfg.get("zones") or []:
            if isinstance(zone, dict) and zone.get("key") == self._zone:
                generation = zone.get("manual_clear")
                if isinstance(generation, int) and not isinstance(generation, bool) and generation > 0:
                    previous_clear = generation
        self.cfg = empty_config()
        self.cfg["zones"] = [{"key": self._zone, **parsed, "manual_clear": previous_clear + 1}]
        if isinstance(actions, list):
            self.cfg["momentary_actions"] = actions
        return parsed["name"]

    def _form(self, current: dict, errors: dict | None):
        return self.async_show_form(step_id="zone", data_schema=self._schema(current), errors=errors or None)

    def _finish(self, title: str):
        cfg = keep_section(normalise(self.cfg), self.cfg)
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

    def _entry_config(self, entry) -> dict:
        options = getattr(entry, "options", None) or {}
        data = getattr(entry, "data", None) or {}
        raw = options.get(CONF_CONFIG) if isinstance(options, dict) else None
        if not isinstance(raw, dict) and isinstance(data, dict):
            raw = data.get(CONF_CONFIG)
        return raw if isinstance(raw, dict) else {}

    def _other_outdoor(self) -> bool:
        getter = getattr(self, "_async_current_entries", None)
        if not callable(getter):
            return False
        own = getattr(getattr(self, "config_entry", None), "entry_id", None)
        for entry in getter() or []:
            if own and getattr(entry, "entry_id", None) == own:
                continue
            if isinstance(self._entry_config(entry).get("outdoor"), dict):
                return True
        return False

    def _outdoor_current(self) -> dict:
        outdoor = self.cfg.get("outdoor") if isinstance(self.cfg, dict) else None
        if isinstance(outdoor, dict):
            return outdoor_block(outdoor)
        return {"radiation": "", "factor": 0.6}

    def _outdoor_schema(self, current: dict) -> vol.Schema:
        radiation, radiation_sel = self._optional(
            "radiation",
            sel.EntitySelector(sel.EntitySelectorConfig(domain="sensor", multiple=False)),
            current.get("radiation") or None,
        )
        factor, factor_sel = self._field(
            "factor",
            sel.NumberSelector(sel.NumberSelectorConfig(min=0, max=100, step=0.1, mode=sel.NumberSelectorMode.BOX)),
            current.get("factor", 0.6),
        )
        return vol.Schema({radiation: radiation_sel, factor: factor_sel})

    def _parse_outdoor(self, user_input: dict) -> tuple[dict | None, dict]:
        radiation = user_input.get("radiation") or ""
        if isinstance(radiation, list):
            radiation = radiation[0] if radiation else ""
        if not isinstance(radiation, str):
            radiation = ""
        errors: dict[str, str] = {}
        if radiation and not radiation.startswith("sensor."):
            errors["radiation"] = "bad_lux"
        factor = user_input.get("factor", 0.6)
        if factor in (None, ""):
            factor = 0.6
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor < 0:
            errors["factor"] = "bad_factor"
        if errors:
            return None, errors
        return outdoor_block({"radiation": radiation, "factor": factor}), {}

    def _finish_outdoor(self):
        cfg = {"version": 5, "zones": [], "outdoor": outdoor_block(self.cfg.get("outdoor") or {})}
        if self._saving:
            entries = getattr(self.hass, "config_entries", None)
            update = getattr(entries, "async_update_entry", None)
            if update is not None and getattr(self, "config_entry", None) is not None:
                update(self.config_entry, title="Outdoor")
            return self.async_create_entry(title="", data={CONF_CONFIG: cfg})
        return self.async_create_entry(title="Outdoor", data={CONF_CONFIG: cfg})

    async def async_step_outdoor(self, user_input=None):
        if not self._saving and self._other_outdoor():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            parsed, errors = self._parse_outdoor(user_input)
            if errors:
                return self.async_show_form(step_id="outdoor", data_schema=self._outdoor_schema(user_input), errors=errors)
            self.cfg = {"version": 5, "zones": [], "outdoor": parsed}
            return self._finish_outdoor()
        return self.async_show_form(step_id="outdoor", data_schema=self._outdoor_schema(self._outdoor_current()))


class AdaptiveLightConfigFlow(_Fields, config_entries.ConfigFlow, domain=DOMAIN):
    """Create one zone. A second zone is a second entry, not a second step."""

    VERSION = 3
    _saving = False

    def __init__(self) -> None:
        self.cfg = empty_config()
        self._zone = ""

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["zone", "outdoor"])

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return AdaptiveLightOptionsFlow()


class AdaptiveLightOptionsFlow(_Fields, config_entries.OptionsFlow):
    """Zone fields, and one momentary action, for this entry only."""

    _saving = True

    def __init__(self) -> None:
        self._loaded = False
        self._zone = ""
        self._action_key: str | None = None
        self.cfg = empty_config()

    def _load(self) -> None:
        if self._loaded:
            return
        raw = self.config_entry.options.get(CONF_CONFIG) or self.config_entry.data.get(CONF_CONFIG) or {}
        self.cfg = copy.deepcopy(raw) if isinstance(raw, dict) else empty_config()
        zones = [zone for zone in self.cfg.get("zones") or [] if isinstance(zone, dict) and zone.get("key")]
        self._zone = zones[0]["key"] if zones else ""
        self._loaded = True

    def _zone_title(self) -> str:
        for zone in self.cfg.get("zones") or []:
            if zone.get("key") == self._zone and zone.get("name"):
                return str(zone["name"])
        return self._zone or "Adaptive Light"

    async def async_step_init(self, user_input=None):
        self._load()
        if isinstance(self.cfg.get("outdoor"), dict) and not self._zone:
            return await self.async_step_outdoor()
        return self.async_show_menu(step_id="init", menu_options=["zone", "momentary"])

    async def async_step_momentary(self, user_input=None):
        self._load()
        actions = parse_actions(self.cfg)
        if len(actions) > 1 and self._action_key is None:
            if user_input is None or ("action" in user_input and "lamp" not in user_input):
                if user_input is not None:
                    choice = user_input.get("action")
                    self._action_key = "" if choice in (None, "new") else str(choice)
                else:
                    options = [action["key"] for action in actions] + ["new"]
                    schema = vol.Schema({
                        vol.Required("action"): sel.SelectSelector(sel.SelectSelectorConfig(
                            options=options, translation_key="momentary_action",
                        )),
                    })
                    return self.async_show_form(step_id="momentary", data_schema=schema)
        elif len(actions) == 1 and self._action_key is None:
            self._action_key = actions[0]["key"]
        if user_input is not None and "action" in user_input and "lamp" not in user_input:
            user_input = None
        return await self._save_momentary(user_input)

    async def _save_momentary(self, user_input):
        current = self._momentary_current()
        if user_input is None:
            return self.async_show_form(step_id="momentary", data_schema=self._momentary_schema(current))
        parsed, errors = self._parse_momentary(user_input)
        if errors:
            return self.async_show_form(
                step_id="momentary", data_schema=self._momentary_schema(self._momentary_submitted(user_input)), errors=errors,
            )
        write_action(self.cfg, self._action_key or None, parsed)
        return self._finish(self._zone_title())

    def _momentary_current(self) -> dict:
        for action in parse_actions(self.cfg):
            if action["key"] == self._action_key:
                return action
        return {"color": "white", "duration": 10, "return": "restore"}

    def _momentary_submitted(self, user_input: dict) -> dict:
        lamps = _as_list(user_input.get("lamp"))
        return {
            "lamp": lamps[0] if lamps else "",
            "color": user_input.get("color") or "",
            "duration": user_input.get("duration", 10),
            "return": user_input.get("return") or "restore",
        }

    def _momentary_schema(self, current: dict) -> vol.Schema:
        lamp, lamp_sel = self._named(
            "lamp",
            sel.EntitySelector(sel.EntitySelectorConfig(domain="light", multiple=False)),
            current.get("lamp") or None,
        )
        color, color_sel = self._named("color", sel.TextSelector(), current.get("color") or "white")
        duration, duration_sel = self._named(
            "duration",
            sel.NumberSelector(sel.NumberSelectorConfig(min=1, max=86400, step=1, unit_of_measurement="s", mode=sel.NumberSelectorMode.BOX)),
            current.get("duration", 10),
        )
        choice, choice_sel = self._named(
            "return",
            sel.SelectSelector(sel.SelectSelectorConfig(
                options=["restore", "off"], translation_key="return_mode",
            )),
            current.get("return") or "restore",
        )
        return vol.Schema({lamp: lamp_sel, color: color_sel, duration: duration_sel, choice: choice_sel})

    def _named(self, label: str, selector, value: Any):
        if value in (None, "", []):
            return vol.Required(label), selector
        return vol.Required(label, description={"suggested_value": value}), selector

    def _parse_momentary(self, user_input: dict) -> tuple[dict | None, dict]:
        lamps = _as_list(user_input.get("lamp"))
        color = str(user_input.get("color") or "").strip()
        duration = _timeout(user_input.get("duration"))
        mode = user_input.get("return")
        errors: dict[str, str] = {}
        if len(lamps) != 1 or not lamps[0].startswith("light."):
            errors["lamp"] = "bad_lamp"
        if not color:
            errors["color"] = "need_color"
        if duration is None or duration < 1:
            errors["duration"] = "bad_duration"
        if mode not in ("restore", "off"):
            errors["return"] = "bad_return"
        if errors:
            return None, errors
        return {"lamp": lamps[0], "color": color, "duration": duration, "return": mode}, {}

"""Setup wizard (zone -> sensors -> lamps -> groups) and the options flow (edit everything, save once)."""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from homeassistant.util import slugify

from . import areas, draft as drafts, flow_ops as ops, params as P
from .const import CONF_CONFIG, DOMAIN
from .model import (BINDING_OFF, BINDING_SLOTS, GLOBAL_ROLES, KEY_RE, ROLE_DOMAINS, ZONE_PRESENCE, empty_config,
                    normalise, plan_living_room, validate)

_LOGGER = logging.getLogger(__name__)
EXTRA_ROLES = ("sofa", "table", "fridge", "entrance", "carpet", "window_advice", "window_left", "window_right",
               "vent_mode", "color_store")
BOOL_OPTS = ["yes", "no"]
# (english, russian). Keys are the step id the menu item opens.
_MENU_TEXT = {
    "groups_menu": {
        "group_add": ("Add a lamp group", "Добавить группу ламп"),
        "zone_detect": ("On detection", "По детекции"),
        "more_zones": ("Continue", "Дальше"),
    },
    "zone_menu": {
        "zone_sensors": ("Sensors", "Датчики"),
        "zone_extra_sensors": ("Extra sensors", "Доп. датчики"),
        "zone_lamps": ("Add lamps", "Добавить лампы"),
        "group_add": ("Add a group", "Добавить группу"),
        "zone_params": ("Zone parameters", "Параметры зоны"),
        "zone_bindings": ("Behaviour scenarios", "Сценарии поведения"),
        "zone_detect": ("On detection", "По детекции"),
        "zone_remove": ("Remove zone", "Удалить зону"),
        "init": ("Back", "Назад"),
    },
    "zone_detect": {
        "detect_add": ("Add a rule", "Добавить правило"),
        "detect_tpl_room": (
            "Template: room presence → main group, adaptive, off 10 min after leave",
            "Шаблон: присутствие комнаты → основная группа, адаптивно, выкл через 10 мин"),
        "detect_tpl_fridge": (
            "Template: fridge sensor → one lamp, fixed 30% at 4500 K, off after 10 s",
            "Шаблон: датчик холодильника → одна лампа, 30% и 4500 K, выкл через 10 с"),
        "detect_delete": ("Delete a rule", "Удалить правило"),
        "zone_menu": ("Back", "Назад"),
        "groups_menu": ("Back", "Назад"),
    },
}


def _ent(domains, multiple=False):
    return sel.EntitySelector(sel.EntitySelectorConfig(domain=list(domains), multiple=multiple))


def _opt(name, current=None):
    if current in (None, "", []):
        return vol.Optional(name)
    return vol.Optional(name, description={"suggested_value": current})


def _param_selector(name: str):
    s = P.PARAMS[name]
    if s.kind == "bool":
        return sel.SelectSelector(sel.SelectSelectorConfig(options=BOOL_OPTS, translation_key="yes_no"))
    if s.kind == "text":
        if name in ("mode_entity", "scene_entity"):
            return _ent(["input_select", "select"])
        return sel.TextSelector()
    cfg = {"min": s.lo, "max": s.hi, "step": s.step, "mode": sel.NumberSelectorMode.BOX}
    if s.unit:
        cfg["unit_of_measurement"] = s.unit
    return sel.NumberSelector(sel.NumberSelectorConfig(**cfg))


def _to_form(name: str, v: Any) -> Any:
    return ("yes" if v else "no") if P.PARAMS[name].kind == "bool" else v


def _from_form(name: str, v: Any) -> Any:
    return int(v == "yes") if P.PARAMS[name].kind == "bool" else v


def params_schema(level: str, current: dict) -> vol.Schema:
    return vol.Schema({_opt(n, _to_form(n, current[n]) if n in current else None): _param_selector(n) for n in P.UI_NAMES[level]})


def summary(cfg: dict) -> str:
    lines = []
    for z in cfg["zones"]:
        lamps = [x["key"] for x in cfg["lamps"] if x["zone"] == z["key"]]
        groups = [g["key"] for g in cfg["groups"] if g["zone"] == z["key"]]
        lines.append(f"{z['name']}: lamps {', '.join(lamps) or '-'}; groups {', '.join(groups) or '-'}")
    return "\n".join(lines) or "-"


class EditorMixin:
    """Steps shared by the wizard and the options flow. Subclasses provide _entry_step (where to go after a form)."""

    cfg: dict
    _zone: str = ""
    _lamp: str = ""
    _group: str = ""
    _level: str = "global"
    _target: str = ""
    _detect_back: str = "zone_menu"
    _detect_prefill: dict | None = None

    def _ru(self) -> bool:
        lang = getattr(getattr(self, "hass", None), "config", None)
        return str(getattr(lang, "language", "en") or "en").lower().startswith("ru")

    def _t(self, en: str, ru: str) -> str:
        return ru if self._ru() else en

    def _menu(self, step: str, options: list[str]) -> dict[str, str]:
        """Labels travel with the step.

        A list of step ids is labelled from the translation files. Home Assistant keeps those
        files in the open browser tab and does not reload them when the integration is updated,
        so a new item renders as an empty row. A dict is shown as-is.
        """
        spec = _MENU_TEXT[step]
        return {key: self._t(*spec[key]) for key in options}

    def _first_zone(self) -> str:
        return self._zone or (self.cfg["zones"][0]["key"] if self.cfg["zones"] else "")

    async def _go(self, step: str):
        await self._checkpoint(step)
        return await getattr(self, f"async_step_{step}")()

    async def _checkpoint(self, next_step: str) -> None:
        """Called whenever the editor moves on; the wizard saves its draft here."""

    # ---- Home Assistant Areas (overridable in tests)
    def _list_areas(self) -> list[tuple[str, str]]:
        return areas.async_list_areas(self.hass)

    def _area_defaults(self, area_id: str) -> dict[str, list[str]]:
        return areas.async_area_defaults(self.hass, area_id)

    def _zone_area_defaults(self) -> dict[str, list[str]]:
        aid = ops.find(self.cfg, "zones", self._zone).get("area_id") if self._zone else None
        return self._area_defaults(aid) if aid else {}

    # ---- sensors
    async def async_step_global_sensors(self, user_input=None):
        if user_input is not None:
            for role in GLOBAL_ROLES:
                ops.set_sensor(self.cfg, role, user_input.get(role))
            return await self._go(self._after_global_sensors)
        cur = {s["role"]: s["entity"] for s in self.cfg["sensors"] if s["zone"] == ""}
        return self.async_show_form(step_id="global_sensors", data_schema=vol.Schema(
            {_opt(r, cur.get(r)): _ent(ROLE_DOMAINS[r]) for r in GLOBAL_ROLES}))

    async def async_step_zone_sensors(self, user_input=None):
        z = self._zone
        if user_input is not None:
            ops.set_sensor(self.cfg, "illuminance", user_input.get("illuminance"), z)
            ops.set_presence(self.cfg, z, user_input.get("presence"))
            ops.set_media(self.cfg, z, user_input.get("media", []))
            return await self._go(self._after_zone_sensors)
        by = lambda r: [s["entity"] for s in self.cfg["sensors"] if s["zone"] == z and s["role"] == r]
        untouched = not any(s["zone"] == z for s in self.cfg["sensors"])
        d = self._zone_area_defaults() if untouched else {}
        ill = (by("illuminance") or d.get("illuminance", [])[:1] or [None])[0]
        pres = by("presence") or d.get("presence") or []
        schema = vol.Schema({
            _opt("illuminance", ill): _ent(["sensor"]),
            _opt("presence", pres): _ent(["binary_sensor"], multiple=True),
            _opt("media", by("media") or d.get("media", [])): _ent(ROLE_DOMAINS["media"], multiple=True)})
        return self.async_show_form(step_id="zone_sensors", data_schema=schema,
                                    description_placeholders={"zone": self._zone_name()})

    async def async_step_zone_extra_sensors(self, user_input=None):
        z = self._zone
        if user_input is not None:
            for r in EXTRA_ROLES:
                ops.set_sensor(self.cfg, r, user_input.get(r), z)
            return await self._go("zone_menu")
        cur = {s["role"]: s["entity"] for s in self.cfg["sensors"] if s["zone"] == z}
        return self.async_show_form(step_id="zone_extra_sensors", data_schema=vol.Schema(
            {_opt(r, cur.get(r)): _ent(ROLE_DOMAINS[r]) for r in EXTRA_ROLES}), description_placeholders={"zone": self._zone_name()})

    def _zone_name(self) -> str:
        return ops.find(self.cfg, "zones", self._zone)["name"] if self._zone else ""

    # ---- lamps and groups
    async def async_step_zone_lamps(self, user_input=None):
        if user_input is not None:
            ops.add_lamps(self.cfg, self._zone, user_input.get("lamps", []))
            return await self._go(self._after_zone_lamps)
        have = {x["entity"] for x in self.cfg["lamps"]}
        suggest = [e for e in self._zone_area_defaults().get("lights", []) if e not in have]
        return self.async_show_form(step_id="zone_lamps", data_schema=vol.Schema({_opt("lamps", suggest): _ent(["light"], True)}),
                                    description_placeholders={"zone": self._zone_name()})

    # ---- new zone: pick an Area (optional), then confirm the name and key
    _pending_area: str | None = None

    async def _zone_area_step(self, step_id: str, user_input):
        used = {z.get("area_id") for z in self.cfg["zones"]}
        free = [(i, n) for i, n in self._list_areas() if i not in used]
        if user_input is not None or not free:
            self._pending_area = (user_input or {}).get("area") or None
            return await self.async_step_zone_name()
        options = [{"value": i, "label": n} for i, n in free]
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema({
            vol.Optional("area"): sel.SelectSelector(sel.SelectSelectorConfig(options=options, mode=sel.SelectSelectorMode.DROPDOWN))}))

    def _default_zone_key(self, name: str, area_id: str | None) -> str:
        return ops.unique_key(area_id or slugify(name) or "zone", {z["key"] for z in self.cfg["zones"]})

    async def async_step_zone_name(self, user_input=None):
        area = self._pending_area
        area_name = dict(self._list_areas()).get(area or "", "")
        errors = {}
        cur = user_input or {}
        if user_input is not None:
            name, key = (cur.get("name") or "").strip(), (cur.get("key") or "").strip()
            if not name:
                errors["base"] = "zone_needs_name"
            elif key and not KEY_RE.match(key):
                errors["base"] = "bad_key"
            elif key and key in {z["key"] for z in self.cfg["zones"]}:
                errors["base"] = "duplicate_key"
            else:
                self._zone = ops.add_zone(self.cfg, name, key or self._default_zone_key(name, area), area)
                self._pending_area = None
                return await self._go("zone_sensors")
        name_s = cur.get("name") or area_name
        key_s = cur.get("key") or (self._default_zone_key(name_s, area) if area else "")
        return self.async_show_form(step_id="zone_name", errors=errors, data_schema=vol.Schema({
            _opt("name", name_s): sel.TextSelector(), _opt("key", key_s): sel.TextSelector()}),
            description_placeholders={"area": area_name or "-"})

    def _zone_lamp_options(self):
        return [x["key"] for x in self.cfg["lamps"] if x["zone"] == self._zone]

    async def async_step_group_add(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("lamps"):
                errors["base"] = "group_needs_lamps"
            else:
                ops.add_group(self.cfg, self._zone, user_input["name"], user_input["lamps"], user_input.get("share_pause", False))
                return await self._go(self._after_group)
        lamps = sel.SelectSelector(sel.SelectSelectorConfig(options=self._zone_lamp_options(), multiple=True))
        return self.async_show_form(step_id="group_add", errors=errors, data_schema=vol.Schema({
            vol.Required("name"): sel.TextSelector(), vol.Required("lamps"): lamps,
            vol.Optional("share_pause", default=False): sel.BooleanSelector()}),
            description_placeholders={"zone": self._zone_name()})

    async def async_step_group_edit(self, user_input=None):
        g = ops.find(self.cfg, "groups", self._group)
        if user_input is not None:
            if not user_input.get("lamps"):
                return self.async_show_form(step_id="group_edit", errors={"base": "group_needs_lamps"}, data_schema=self._group_schema(g, user_input))
            g["name"], g["lamps"], g["share_pause"] = user_input["name"], user_input["lamps"], user_input.get("share_pause", False)
            return await self._go("group_menu")
        return self.async_show_form(step_id="group_edit", data_schema=self._group_schema(g, g),
                                    description_placeholders={"group": g["name"]})

    def _group_schema(self, g, cur):
        opts = [x["key"] for x in self.cfg["lamps"] if x["zone"] == g["zone"]]
        return vol.Schema({
            vol.Required("name", description={"suggested_value": cur.get("name", g["name"])}): sel.TextSelector(),
            vol.Required("lamps", description={"suggested_value": cur.get("lamps", g["lamps"])}):
                sel.SelectSelector(sel.SelectSelectorConfig(options=opts, multiple=True)),
            vol.Optional("share_pause", default=cur.get("share_pause", g["share_pause"])): sel.BooleanSelector()})

    # ---- parameters
    async def async_step_params(self, user_input=None):
        cur = ops.overrides(self.cfg, self._level, self._target)
        if user_input is not None:
            ops.set_overrides(self.cfg, self._level, self._target,
                              {n: _from_form(n, v) for n, v in user_input.items() if v not in (None, "")})
            return await self._go(self._after_params)
        return self.async_show_form(step_id="params", data_schema=params_schema(self._level, cur),
                                    description_placeholders={"level": self._level, "target": self._target or "-"})

    def _binding_selector(self, keys: list[str]):
        off = self._t("Off", "Выкл")
        options = [{"value": BINDING_OFF, "label": off}, *[{"value": k, "label": k} for k in keys]]
        return sel.SelectSelector(sel.SelectSelectorConfig(options=options, mode=sel.SelectSelectorMode.DROPDOWN))

    async def async_step_zone_bindings(self, user_input=None):
        z = ops.find(self.cfg, "zones", self._zone)
        if user_input is not None:
            z["bindings"] = ops.bindings_from_form(user_input)
            return await self._go("zone_menu")
        groups = [g["key"] for g in self.cfg["groups"] if g["zone"] == self._zone]
        lamps = self._zone_lamp_options()
        fields = {}
        for slot, kind in BINDING_SLOTS.items():
            opts = groups if kind == "group" else lamps
            fields[_opt(slot, z["bindings"].get(slot))] = self._binding_selector(opts)
        return self.async_show_form(step_id="zone_bindings", data_schema=vol.Schema(fields),
                                    description_placeholders={"zone": z["name"]})

    # ---- detection rules (turn lamps on, and off after a delay; empty list = no detection control)
    def _binary_sensors(self) -> list[str]:
        out: list[str] = []
        for s in self.cfg["sensors"]:
            ent = s.get("entity") or ""
            if s.get("zone") == self._zone and ent.startswith("binary_sensor.") and ent not in out:
                out.append(ent)
        return out

    def _detect_labels(self) -> dict[str, str]:
        """Schema names are the labels. A missing translation then cannot fall back to a raw key."""
        return {
            "sensor": self._t("Trigger", "Триггер"),
            "lamps": self._t("Lamps", "Лампы"),
            "brightness": self._t("Brightness", "Яркость"),
            "brightness_pct": self._t("Brightness, %", "Яркость, %"),
            "color_temp_kelvin": self._t("Colour temperature, K", "Цветовая температура, K"),
            "off_delay_s": self._t("Off delay, s", "Задержка выключения, с"),
            "rule": self._t("Rule", "Правило"),
        }

    def _read_form(self, user_input: dict) -> dict:
        rev = {label: key for key, label in self._detect_labels().items()}
        return {rev.get(k, k): v for k, v in user_input.items()}

    def _mark(self, name: str, *, required: bool = False, suggested=None, visible=None):
        cls = vol.Required if required else vol.Optional
        description = None if suggested in (None, "", []) else {"suggested_value": suggested}
        if visible is not None:
            try:
                if description is None:
                    return cls(name, visible=visible)
                return cls(name, description=description, visible=visible)
            except TypeError:
                pass
        if description is None:
            return cls(name)
        return cls(name, description=description)

    def _fixed_fields_live(self) -> bool:
        """Home Assistant 2026 hides a field while its marker `visible` condition is false."""
        try:
            vol.Optional("_probe", visible={"field": "brightness", "value": "fixed"})
        except TypeError:
            return False
        return True

    def _trigger_options(self) -> list[dict]:
        presence = self._t("Zone presence (any sensor)", "Присутствие зоны (любой датчик)")
        zone = self._binary_sensors()
        seen = set(zone)
        options = [{"value": ZONE_PRESENCE, "label": presence}, *[{"value": e, "label": e} for e in zone]]
        states = getattr(self.hass, "states", None)
        if states is not None and hasattr(states, "async_all"):
            try:
                found = states.async_all("binary_sensor")
            except Exception:
                found = []
            for st in found:
                eid = getattr(st, "entity_id", "") or ""
                if eid.startswith("binary_sensor.") and eid not in seen:
                    seen.add(eid)
                    name = getattr(st, "name", None) or eid
                    label = eid if name in (None, "", eid) else f"{name} ({eid})"
                    options.append({"value": eid, "label": label})
        return options

    def _rules_note(self, rules: list[dict]) -> str:
        if not rules:
            return self._t("No detection rules.", "Правил по детекции нет.")
        return "\n".join(
            f"{r['id']}: {r.get('sensor')} → {', '.join(r.get('lamps') or [])} ({r.get('brightness')}, {r.get('off_delay_s')} s)"
            for r in rules)

    def _detect_schema(self, cur: dict) -> vol.Schema:
        lab = self._detect_labels()
        mode = cur.get("brightness") or "adaptive"
        delay = cur.get("off_delay_s", 0)
        box = sel.NumberSelectorMode.BOX
        live = self._fixed_fields_live()
        fixed_only = {"field": lab["brightness"], "value": "fixed"}
        brightness_options = [
            {"value": "adaptive", "label": self._t(
                "Adaptive (the controller sets the brightness)", "Адаптивная (яркость задаёт контроллер)")},
            {"value": "fixed", "label": self._t("Fixed", "Фиксированная")},
        ]
        fields = {
            self._mark(lab["sensor"], required=True, suggested=cur.get("sensor") or ZONE_PRESENCE): sel.SelectSelector(
                sel.SelectSelectorConfig(options=self._trigger_options(), mode=sel.SelectSelectorMode.DROPDOWN,
                                          custom_value=True)),
            self._mark(lab["lamps"], required=True, suggested=cur.get("lamps") or []): sel.SelectSelector(
                sel.SelectSelectorConfig(options=self._zone_lamp_options(), multiple=True)),
            self._mark(lab["brightness"], required=True, suggested=mode): sel.SelectSelector(
                sel.SelectSelectorConfig(options=brightness_options, mode=sel.SelectSelectorMode.LIST)),
        }
        if live or mode == "fixed":
            fields[self._mark(lab["brightness_pct"], required=True, suggested=cur.get("brightness_pct", 30),
                              visible=fixed_only if live else None)] = sel.NumberSelector(
                sel.NumberSelectorConfig(min=1, max=100, step=1, mode=box, unit_of_measurement="%"))
            fields[self._mark(lab["color_temp_kelvin"], suggested=cur.get("color_temp_kelvin"),
                              visible=fixed_only if live else None)] = sel.NumberSelector(
                sel.NumberSelectorConfig(min=1000, max=20000, step=50, mode=box, unit_of_measurement="K"))
        fields[self._mark(lab["off_delay_s"], required=True, suggested=0 if delay in (None, "") else int(delay))] = (
            sel.NumberSelector(sel.NumberSelectorConfig(min=0, max=86400, step=1, mode=box, unit_of_measurement="s")))
        return vol.Schema(fields)

    async def async_step_zone_detect(self, user_input=None):
        z = ops.find(self.cfg, "zones", self._zone)
        rules = z.get("detect_rules") or []
        opts = ["detect_add", "detect_tpl_room", "detect_tpl_fridge"]
        if rules:
            opts.append("detect_delete")
        opts.append(self._detect_back)
        return self.async_show_menu(
            step_id="zone_detect", menu_options=self._menu("zone_detect", opts),
            description_placeholders={"zone": z["name"], "rules": self._rules_note(rules)})

    async def async_step_detect_add(self, user_input=None):
        pre = dict(self._detect_prefill or {})
        self._detect_prefill = None
        errors: dict = {}
        shown = pre
        if user_input is not None:
            form = self._read_form(user_input)
            shown = form
            sensor = form.get("sensor")
            lamps = ops.as_entities(form.get("lamps"))
            brightness = form.get("brightness")
            known = set(self._zone_lamp_options())
            if not sensor or (sensor != ZONE_PRESENCE and not str(sensor).startswith("binary_sensor.")):
                errors["base"] = "detect_needs_sensor"
            elif not lamps or any(k not in known for k in lamps):
                errors["base"] = "detect_needs_lamps"
            elif brightness not in ("adaptive", "fixed"):
                errors["base"] = "detect_needs_brightness"
            elif brightness == "fixed" and form.get("brightness_pct") in (None, ""):
                shown = {**form, "brightness": "fixed"}
            else:
                pct = form.get("brightness_pct")
                kelvin = form.get("color_temp_kelvin")
                try:
                    delay = int(form.get("off_delay_s") if form.get("off_delay_s") not in (None, "") else 0)
                except (TypeError, ValueError):
                    delay = -1
                if delay < 0:
                    errors["base"] = "bad_detect_rule"
                else:
                    rid = ops.add_detect_rule(
                        self.cfg, self._zone, sensor=sensor, lamps=lamps, brightness=brightness, off_delay_s=delay,
                        brightness_pct=None if brightness != "fixed" or pct in (None, "") else int(pct),
                        color_temp_kelvin=None if brightness != "fixed" or kelvin in (None, "") else int(kelvin))
                    if self._errors():
                        ops.delete_detect_rule(self.cfg, self._zone, rid)
                        errors = self._errors()
                    else:
                        return await self._go("zone_detect")
        return self.async_show_form(
            step_id="detect_add", data_schema=self._detect_schema(shown), errors=errors,
            description_placeholders={"zone": self._zone_name()})

    async def async_step_detect_tpl_room(self, user_input=None):
        pre = ops.room_template(self.cfg, self._zone)
        if not pre:
            return self.async_show_form(
                step_id="detect_add", errors={"base": "detect_needs_lamps"}, data_schema=self._detect_schema({}),
                description_placeholders={"zone": self._zone_name()})
        self._detect_prefill = pre
        return await self.async_step_detect_add()

    async def async_step_detect_tpl_fridge(self, user_input=None):
        pre = ops.fridge_template(self.cfg, self._zone)
        if not pre:
            has_fridge = any(s.get("role") == "fridge" and s.get("zone") == self._zone for s in self.cfg["sensors"])
            return self.async_show_form(
                step_id="detect_add", errors={"base": "detect_needs_lamps" if has_fridge else "detect_no_fridge_sensor"},
                data_schema=self._detect_schema({}), description_placeholders={"zone": self._zone_name()})
        self._detect_prefill = pre
        return await self.async_step_detect_add()

    async def async_step_detect_delete(self, user_input=None):
        rules = ops.find(self.cfg, "zones", self._zone).get("detect_rules") or []
        picked = None
        if user_input is not None:
            form = self._read_form(user_input)
            picked = form.get("rule")
        if picked:
            ops.delete_detect_rule(self.cfg, self._zone, picked)
            return await self._go("zone_detect")
        if not rules:
            return await self._go("zone_detect")
        options = [{"value": r["id"], "label": f"{r['id']}: {r.get('sensor')} → {', '.join(r.get('lamps') or [])}"} for r in rules]
        return self.async_show_form(
            step_id="detect_delete", description_placeholders={"zone": self._zone_name()},
            data_schema=vol.Schema({
                self._mark(self._detect_labels()["rule"], required=True): sel.SelectSelector(
                    sel.SelectSelectorConfig(options=options))}))

    # ---- final check
    def _errors(self) -> dict:
        errs = validate(normalise(self.cfg))
        return {"base": errs[0].code} if errs else {}

    async def async_step_save(self, user_input=None):
        errors = self._errors()
        if user_input is not None and not errors:
            return await self._finish(normalise(self.cfg))
        return self.async_show_form(step_id="save", data_schema=vol.Schema({}), errors=errors,
                                    description_placeholders={"summary": summary(self.cfg), "detail": self._detail()})

    def _detail(self) -> str:
        errs = validate(normalise(self.cfg))
        return errs[0].detail if errs else ""


class AdaptiveLightConfigFlow(EditorMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    _after_global_sensors = "zone"
    _after_zone_sensors = "zone_lamps"
    _after_zone_lamps = "groups_menu"
    _after_group = "groups_menu"
    _after_params = "save"
    _detect_back = "groups_menu"

    def __init__(self) -> None:
        self.cfg = empty_config()
        self._draft: dict | None = None

    # ---- draft: progress survives an accidentally closed dialog (see draft.py)
    def _drafts(self) -> drafts.DraftStore:
        return drafts.HassDraftStore(self.hass)

    def _now_ts(self) -> float:
        return time.time()

    async def _checkpoint(self, next_step: str) -> None:
        if not drafts.is_meaningful(self.cfg):
            return
        try:
            await self._drafts().async_save(drafts.pack(self.cfg, next_step, self._zone, self._now_ts()))
        except Exception:  # a failing draft must never break the wizard itself
            _LOGGER.warning("could not save the wizard draft", exc_info=True)

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        self._draft = drafts.unpack(await self._drafts().async_load(), self._now_ts())
        options = ["global_sensors", "preset_plan"]
        note = ""
        if self._draft:
            c = self._draft["config"] = normalise(self._draft["config"])
            options = ["resume_draft", *options, "discard_draft"]
            head = ("Найдена незавершённая настройка: зон %d, ламп %d." if self.hass.config.language.startswith("ru")
                    else "Unfinished setup found: %d zone(s), %d lamp(s).") % (len(c["zones"]), len(c["lamps"]))
            note = f"{head}\n{summary(c)}"
        return self.async_show_menu(step_id="user", menu_options=options, description_placeholders={"draft": note})

    async def async_step_resume_draft(self, user_input=None):
        if not self._draft:
            return await self.async_step_user()
        self.cfg, self._zone = copy.deepcopy(self._draft["config"]), self._draft["zone"]
        return await self._go(self._draft["step"])

    async def async_step_discard_draft(self, user_input=None):
        await self._drafts().async_remove()
        self._draft = None
        return await self.async_step_user()

    async def async_step_preset_plan(self, user_input=None):
        self.cfg = plan_living_room()
        return await self._go("save")

    async def async_step_zone(self, user_input=None):
        return await self._zone_area_step("zone", user_input)

    async def async_step_groups_menu(self, user_input=None):
        return self.async_show_menu(
            step_id="groups_menu",
            menu_options=self._menu("groups_menu", ["group_add", "zone_detect", "more_zones"]))

    async def async_step_more_zones(self, user_input=None):
        return self.async_show_menu(step_id="more_zones", menu_options=["zone", "save"])

    async def _finish(self, cfg: dict):
        await self._drafts().async_remove()
        return self.async_create_entry(title="Adaptive Light", data={CONF_CONFIG: cfg})

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return AdaptiveLightOptionsFlow()


class AdaptiveLightOptionsFlow(EditorMixin, config_entries.OptionsFlow):
    _after_global_sensors = "init"
    _after_zone_sensors = "zone_menu"
    _after_zone_lamps = "zone_menu"
    _after_group = "zone_menu"
    _detect_back = "zone_menu"
    _after_params = "init"

    def __init__(self) -> None:
        self._loaded = False

    def _load(self) -> None:
        if not self._loaded:
            self.cfg = copy.deepcopy(self.config_entry.options.get(CONF_CONFIG) or self.config_entry.data[CONF_CONFIG])
            self._loaded = True

    async def _finish(self, cfg: dict):
        return self.async_create_entry(title="", data={CONF_CONFIG: cfg})

    async def async_step_init(self, user_input=None):
        self._load()
        return self.async_show_menu(step_id="init", menu_options=[
            "global_sensors", "global_params", "pick_zone", "new_zone", "pick_lamp", "pick_group", "save"])

    async def async_step_global_params(self, user_input=None):
        self._level, self._target = "global", ""
        self._after_params = "init"
        return await self.async_step_params()

    async def _pick(self, step: str, field: str, options: list[str], attr: str, nxt: str, user_input):
        if user_input is not None:
            setattr(self, attr, user_input[field])
            return await self._go(nxt)
        if not options:
            return await self.async_step_init()
        return self.async_show_form(step_id=step, data_schema=vol.Schema(
            {vol.Required(field): sel.SelectSelector(sel.SelectSelectorConfig(options=options))}))

    async def async_step_pick_zone(self, user_input=None):
        return await self._pick("pick_zone", "zone", [z["key"] for z in self.cfg["zones"]], "_zone", "zone_menu", user_input)

    async def async_step_pick_lamp(self, user_input=None):
        return await self._pick("pick_lamp", "lamp", [x["key"] for x in self.cfg["lamps"]], "_lamp", "lamp_menu", user_input)

    async def async_step_pick_group(self, user_input=None):
        return await self._pick("pick_group", "group", [g["key"] for g in self.cfg["groups"]], "_group", "group_menu", user_input)

    async def async_step_new_zone(self, user_input=None):
        return await self._zone_area_step("new_zone", user_input)

    async def async_step_zone_menu(self, user_input=None):
        return self.async_show_menu(
            step_id="zone_menu",
            menu_options=self._menu("zone_menu", [
                "zone_sensors", "zone_extra_sensors", "zone_lamps", "group_add", "zone_params", "zone_bindings",
                "zone_detect", "zone_remove", "init"]),
            description_placeholders={"zone": self._zone_name()})

    async def async_step_zone_params(self, user_input=None):
        self._level, self._target = "zone", self._zone
        self._after_params = "zone_menu"
        return await self.async_step_params()

    async def async_step_zone_remove(self, user_input=None):
        if user_input is not None:
            ops.remove_zone(self.cfg, self._zone)
            self._zone = ""
            return await self.async_step_init()
        return self.async_show_form(step_id="zone_remove", data_schema=vol.Schema({}),
                                    description_placeholders={"zone": self._zone_name()})

    async def async_step_lamp_menu(self, user_input=None):
        return self.async_show_menu(step_id="lamp_menu", menu_options=["lamp_params", "lamp_remove", "init"],
                                    description_placeholders={"lamp": self._lamp})

    async def async_step_lamp_params(self, user_input=None):
        self._level, self._target = "lamp", self._lamp
        self._after_params = "lamp_menu"
        return await self.async_step_params()

    async def async_step_lamp_remove(self, user_input=None):
        if user_input is not None:
            ops.remove_lamp(self.cfg, self._lamp)
            return await self.async_step_init()
        return self.async_show_form(step_id="lamp_remove", data_schema=vol.Schema({}), description_placeholders={"lamp": self._lamp})

    async def async_step_group_menu(self, user_input=None):
        return self.async_show_menu(step_id="group_menu", menu_options=["group_edit", "group_params", "group_remove", "init"],
                                    description_placeholders={"group": self._group})

    async def async_step_group_params(self, user_input=None):
        self._level, self._target = "group", self._group
        self._after_params = "group_menu"
        return await self.async_step_params()

    async def async_step_group_remove(self, user_input=None):
        if user_input is not None:
            ops.remove_group(self.cfg, self._group)
            return await self.async_step_init()
        return self.async_show_form(step_id="group_remove", data_schema=vol.Schema({}), description_placeholders={"group": self._group})

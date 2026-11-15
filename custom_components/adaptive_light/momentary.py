"""Short color gestures that do not belong to a zone.

A zone never imports this module. Both may call ``light.turn_on`` on the same lamp;
whichever call lands last is the one the lamp keeps. The snapshot for a gesture is
taken at the trigger, so a later zone write does not erase what this gesture will
restore.

A new trigger while a gesture is already active snapshots the lamp as it is now,
not the state from before the first gesture.
"""
from __future__ import annotations

import re
from typing import Any, Callable

SERVICE_MOMENTARY = "momentary_action"
DATA_MOMENTARY = "adaptive_light_momentary"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_COLOR_ATTR = {
    "hs": "hs_color",
    "rgb": "rgb_color",
    "xy": "xy_color",
    "color_temp": "color_temp_kelvin",
}


def parse_actions(raw: Any) -> list[dict]:
    """Read the ``momentary_actions`` section. Invalid items are dropped."""
    items = raw.get("momentary_actions") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    used: set[str] = set()
    out: list[dict] = []
    for item in items:
        action = _action(item, used)
        if action is None:
            continue
        used.add(action["key"])
        out.append(action)
    return out


def keep_section(cfg: dict, source: Any) -> dict:
    """Put a parsed momentary section back next to zones. No-op when there is none."""
    actions = parse_actions(source)
    if not actions:
        return cfg
    stored = dict(cfg)
    stored["momentary_actions"] = [_public(action) for action in actions]
    return stored


def _public(action: dict) -> dict:
    out = {
        "key": action["key"],
        "lamp": action["lamp"],
        "color": action["color"],
        "duration": action["duration"],
        "return": action["return"],
    }
    if action["trigger_type"] in ("sensor", "button"):
        out["trigger"] = {"type": action["trigger_type"], "entity_id": action["trigger_entity"]}
    return out


def _action(raw: Any, used: set[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    lamp = raw.get("lamp")
    color = raw.get("color")
    duration = _seconds(raw.get("duration"))
    mode = raw.get("return")
    if not isinstance(lamp, str) or not lamp.startswith("light.") or lamp == "light.":
        return None
    if not isinstance(color, str) or not color.strip():
        return None
    if duration is None or duration < 1:
        return None
    if mode not in ("restore", "off"):
        return None
    trigger = _trigger(raw.get("trigger"))
    if trigger is None:
        return None
    kind, entity = trigger
    key = raw.get("key")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        key = _slug(lamp)
    key = _unique(key, used)
    return {
        "key": key,
        "lamp": lamp,
        "color": color.strip(),
        "duration": duration,
        "return": mode,
        "trigger_type": kind,
        "trigger_entity": entity,
    }


def _trigger(value: Any) -> tuple[str, str | None] | None:
    if value is None:
        return "service", None
    if isinstance(value, str):
        return _trigger_entity(value)
    if isinstance(value, dict):
        kind = value.get("type", value.get("platform"))
        if kind == "service":
            return "service", None
        entity = value.get("entity_id")
        if kind == "sensor" and isinstance(entity, str) and entity.startswith("binary_sensor."):
            return "sensor", entity
        if kind == "button" and isinstance(entity, str) and entity.startswith("button."):
            return "button", entity
        return None
    return None


def _trigger_entity(entity: str) -> tuple[str, str] | None:
    if entity.startswith("binary_sensor."):
        return "sensor", entity
    if entity.startswith("button."):
        return "button", entity
    return None


def _seconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _slug(lamp: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", lamp.split(".", 1)[-1].lower()).strip("_")
    if not base or not base[0].isalpha():
        base = "action"
    return base[:41]


def _unique(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    n = 2
    while f"{base[:38]}_{n}" in used:
        n += 1
    return f"{base[:38]}_{n}"


def _read(hass, lamp: str) -> dict:
    """One read of the live lamp. Stored before any service call."""
    state = hass.states.get(lamp)
    if state is None:
        return {"state": "off"}
    attrs = getattr(state, "attributes", None) or {}
    shot = {"state": state.state}
    for name in ("brightness", "color_mode", "hs_color", "rgb_color", "xy_color", "color_temp_kelvin"):
        if name in attrs:
            shot[name] = attrs[name]
    return shot


def _restore_data(lamp: str, shot: dict) -> tuple[str, dict]:
    if shot.get("state") != "on":
        return "turn_off", {"entity_id": lamp}
    data: dict[str, Any] = {"entity_id": lamp}
    if "brightness" in shot:
        data["brightness"] = shot["brightness"]
    attr = _COLOR_ATTR.get(shot.get("color_mode"))
    if attr and attr in shot:
        data[attr] = shot[attr]
    return "turn_on", data


class MomentaryActions:
    """Active gestures for one config entry."""

    def __init__(self, hass, raw: dict, call_later: Callable | None = None) -> None:
        self.hass = hass
        self._call_later = call_later
        self.actions = {action["key"]: action for action in parse_actions(raw)}
        self.active: dict[str, dict] = {}
        self._timers: dict[str, Callable[[], None]] = {}
        self._unsubs: list[Callable[[], None]] = []

    def same(self, raw: Any) -> bool:
        return parse_actions(raw) == list(self.actions.values())

    async def trigger(self, key: str) -> bool:
        spec = self.actions.get(key)
        if spec is None:
            return False
        self.active[key] = _read(self.hass, spec["lamp"])
        self._arm(key)
        await self._call("turn_on", {"entity_id": spec["lamp"], "color_name": spec["color"]})
        return True

    async def finish(self, key: str) -> None:
        spec = self.actions.get(key)
        shot = self.active.pop(key, None)
        self._cancel(key)
        if spec is None or shot is None:
            return
        if spec["return"] == "off":
            await self._call("turn_off", {"entity_id": spec["lamp"]})
            return
        service, data = _restore_data(spec["lamp"], shot)
        await self._call(service, data)

    def _arm(self, key: str) -> None:
        self._cancel(key)
        delay = float(self.actions[key]["duration"])

        async def _fire(_now: Any = None) -> None:
            self._timers.pop(key, None)
            await self.finish(key)

        schedule = self._call_later
        if schedule is None:
            from homeassistant.helpers.event import async_call_later

            schedule = async_call_later
        self._timers[key] = schedule(self.hass, delay, _fire)

    def _cancel(self, key: str) -> None:
        cancel = self._timers.pop(key, None)
        if cancel is not None:
            cancel()

    async def _call(self, service: str, data: dict) -> None:
        await self.hass.services.async_call("light", service, data)

    async def _on_event(self, event) -> None:
        entity = event.data.get("entity_id")
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if new is None:
            return
        for spec in self.actions.values():
            if spec.get("trigger_entity") != entity:
                continue
            if spec["trigger_type"] == "sensor":
                if new.state == "on" and (old is None or old.state != "on"):
                    await self.trigger(spec["key"])
            elif spec["trigger_type"] == "button" and new.state not in ("unavailable", "unknown"):
                if old is None or old.state != new.state:
                    await self.trigger(spec["key"])

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_state_change_event

        entities = sorted({spec["trigger_entity"] for spec in self.actions.values() if spec.get("trigger_entity")})
        if entities:
            self._unsubs.append(async_track_state_change_event(self.hass, entities, self._on_event))

    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for key in list(self._timers):
            self._cancel(key)

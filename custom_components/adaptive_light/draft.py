"""Progress of the setup wizard, saved after every step so an accidental close does not lose the work.

Home Assistant discards an unfinished config flow when its dialog is closed, and a config entry can only be created
once, at the end. So the wizard keeps a *draft* in ``.storage`` (outside any config entry) and offers to resume it the
next time the integration is added. The draft holds the configuration being built and the step to continue from.
Pure helpers here; ``HassDraftStore`` is the thin adapter over homeassistant.helpers.storage.Store.
"""
from __future__ import annotations

import copy
from typing import Any, Protocol

DRAFT_VERSION = 1
DRAFT_MAX_AGE_S = 7 * 86400
DRAFT_KEY = "adaptive_light_wizard_draft"
# steps worth resuming at; the entry points of the wizard are not (nothing was entered yet)
RESUMABLE = ("zone", "zone_name", "zone_sensors", "zone_lamps", "groups_menu", "group_add", "more_zones", "params", "save")


def pack(cfg: dict, step: str, zone: str, now_ts: float) -> dict[str, Any]:
    return {"version": DRAFT_VERSION, "ts": now_ts, "step": step, "zone": zone, "config": copy.deepcopy(cfg)}


def is_meaningful(cfg: dict) -> bool:
    return bool(cfg.get("zones") or cfg.get("sensors"))


def unpack(data: Any, now_ts: float, max_age_s: float = DRAFT_MAX_AGE_S) -> dict[str, Any] | None:
    """The draft if it is usable (right shape, not expired, something entered), else None. Never raises."""
    if not isinstance(data, dict) or data.get("version") != DRAFT_VERSION:
        return None
    cfg, ts = data.get("config"), data.get("ts")
    if not isinstance(cfg, dict) or isinstance(ts, bool) or not isinstance(ts, (int, float)) or now_ts - ts > max_age_s:
        return None
    for k in ("zones", "sensors", "lamps", "groups"):
        items = cfg.get(k, [])
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            return None
    if any(not isinstance(x.get("key"), str) for k in ("zones", "lamps", "groups") for x in cfg.get(k, [])):
        return None
    if not is_meaningful(cfg):
        return None
    step, zone = data.get("step"), data.get("zone")
    zones = {z.get("key") for z in cfg.get("zones", []) if isinstance(z, dict)}
    if step not in RESUMABLE:
        step = "more_zones" if cfg.get("zones") else "global_sensors"
    if not isinstance(zone, str) or zone not in zones:
        zone = ""
    if step in ("zone_sensors", "zone_lamps", "groups_menu", "group_add") and not zone:
        step = "more_zones" if zones else "global_sensors"
    return {"config": copy.deepcopy(cfg), "step": step, "zone": zone, "ts": ts}


class DraftStore(Protocol):
    async def async_load(self) -> Any: ...
    async def async_save(self, data: Any) -> None: ...
    async def async_remove(self) -> None: ...


class MemoryDraftStore:
    def __init__(self, data: Any = None):
        self.data = data

    async def async_load(self) -> Any:
        return copy.deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        self.data = copy.deepcopy(data)

    async def async_remove(self) -> None:
        self.data = None


class HassDraftStore:
    def __init__(self, hass):
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, DRAFT_VERSION, DRAFT_KEY)

    async def async_load(self) -> Any:
        return await self._store.async_load()

    async def async_save(self, data: Any) -> None:
        await self._store.async_save(data)

    async def async_remove(self) -> None:
        await self._store.async_remove()

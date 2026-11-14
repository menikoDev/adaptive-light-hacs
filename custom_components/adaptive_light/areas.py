"""Home Assistant Areas as a source of defaults for the "new zone" step.

``pick_defaults`` is pure (duck-typed registry entries, tests use SimpleNamespace). ``async_list_areas`` and
``async_area_entries`` are the thin glue over the area, device and entity registries (imported lazily).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

PRESENCE_CLASSES = ("occupancy", "presence", "motion")  # in order of preference


def pick_defaults(entries: Iterable[Any], attrs: Callable[[str], dict] | None = None) -> dict[str, list[str]]:
    """Suggested lamps and sensors out of the entities of an area.

    lights        every ``light`` entity except light groups (a group entity would double-count its members)
    illuminance   ``sensor`` entities with device class illuminance
    presence      ``binary_sensor`` entities with device class occupancy, presence or motion (best class first)
    media         ``media_player`` entities
    Disabled, hidden and diagnostic/config entities are skipped.
    """
    attrs = attrs or (lambda _eid: {})
    lights: list[str] = []
    ill: list[str] = []
    pres: list[tuple[int, str]] = []
    media: list[str] = []
    for e in sorted(entries, key=lambda x: x.entity_id):
        if getattr(e, "disabled_by", None) or getattr(e, "hidden_by", None) or getattr(e, "entity_category", None):
            continue
        eid = e.entity_id
        domain = eid.split(".", 1)[0]
        a = attrs(eid) or {}
        dev_class = getattr(e, "device_class", None) or getattr(e, "original_device_class", None) or a.get("device_class")
        if domain == "light":
            if not a.get("entity_id"):
                lights.append(eid)
        elif domain == "sensor" and dev_class == "illuminance":
            ill.append(eid)
        elif domain == "binary_sensor" and dev_class in PRESENCE_CLASSES:
            pres.append((PRESENCE_CLASSES.index(dev_class), eid))
        elif domain == "media_player":
            media.append(eid)
    return {"lights": lights, "illuminance": ill, "presence": [eid for _, eid in sorted(pres)], "media": media}


def async_list_areas(hass) -> list[tuple[str, str]]:
    """(area_id, name) of every Area, sorted by name."""
    from homeassistant.helpers import area_registry as ar

    return sorted(((a.id, a.name) for a in ar.async_get(hass).async_list_areas()), key=lambda x: x[1].casefold())


def async_area_entries(hass, area_id: str) -> list[Any]:
    """Entity registry entries of an area: entities placed in it directly plus those of its devices that have no area of their own."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    out = {e.entity_id: e for e in er.async_entries_for_area(ent_reg, area_id)}
    for dev in dr.async_entries_for_area(dev_reg, area_id):
        for e in er.async_entries_for_device(ent_reg, dev.id):
            if e.area_id is None:
                out.setdefault(e.entity_id, e)
    return list(out.values())


def async_area_defaults(hass, area_id: str) -> dict[str, list[str]]:
    def attrs(eid: str) -> dict:
        st = hass.states.get(eid)
        return dict(st.attributes) if st is not None else {}

    return pick_defaults(async_area_entries(hass, area_id), attrs)

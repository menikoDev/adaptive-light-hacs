"""The real ConfigFlow / OptionsFlow classes driven step by step with a stub hass (needs the homeassistant package;
skipped without it: run with the harness venv, /tmp/hatest/venv/bin/python -m pytest).

Covers: optional illuminance, the area step (names, keys, prefilled lamps and sensors), the wizard draft (save after every
step, resume, discard, cleared on finish). What it does NOT cover: the real area/entity registries and Store (stand B does)."""
import asyncio, importlib.util, os, sys, types

import pytest

pytest.importorskip("homeassistant")
import voluptuous as vol  # noqa: E402

import conftest  # noqa: E402,F401
from adaptive_light import draft  # noqa: E402

_spec = importlib.util.spec_from_file_location("adaptive_light.config_flow", os.path.join(conftest.ROOT, "custom_components", "adaptive_light", "config_flow.py"))
cf = importlib.util.module_from_spec(_spec); sys.modules["adaptive_light.config_flow"] = cf; _spec.loader.exec_module(cf)

AREAS = [("living_room", "Гостиная"), ("bedroom", "Bedroom")]
DEFAULTS = {"bedroom": {"lights": ["light.bed_a", "light.bed_b"], "illuminance": ["sensor.bed_lux"], "presence": ["binary_sensor.bed_occ"],
                        "media": ["media_player.bed_tv"]}}
HASS = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))


def run(c): return asyncio.run(c)


class Wizard(cf.AdaptiveLightConfigFlow):
    def __init__(self, store, areas=AREAS, now=1000.0):
        super().__init__()
        self.hass, self.store, self.areas, self.now = HASS, store, areas, now

    def _async_current_entries(self, include_ignore=None): return []
    def _list_areas(self): return self.areas
    def _area_defaults(self, area_id): return DEFAULTS.get(area_id, {})
    def _drafts(self): return self.store
    def _now_ts(self): return self.now


class Options(cf.AdaptiveLightOptionsFlow):
    def __init__(self, cfg, areas=AREAS):
        super().__init__()
        self.hass, self.areas = HASS, areas
        self._entry = types.SimpleNamespace(options={}, data={cf.CONF_CONFIG: cfg})

    @property
    def config_entry(self): return self._entry
    def _list_areas(self): return self.areas
    def _area_defaults(self, area_id): return DEFAULTS.get(area_id, {})


def fields(result):
    """name -> suggested value of a form's schema."""
    out = {}
    for k in result["data_schema"].schema:
        out[str(k)] = (k.description or {}).get("suggested_value")
    return out


def sub(flow, step, data=None):
    return run(getattr(flow, f"async_step_{step}")(data))


# ---------------------------------------------------------------- illuminance is optional

def test_zone_sensors_form_has_no_required_field_and_accepts_an_empty_submit():
    f = Wizard(draft.MemoryDraftStore())
    sub(f, "zone", {}); r = sub(f, "zone_name", {"name": "Hall"})
    assert r["type"] == "form" and r["step_id"] == "zone_sensors"
    assert not [k for k in r["data_schema"].schema if isinstance(k, vol.Required)]
    r = sub(f, "zone_sensors", {})
    assert r["step_id"] == "zone_lamps" and not r.get("errors")
    assert f.cfg["sensors"] == []


def test_zone_sensors_stores_illuminance_when_given_and_clears_it_when_removed():
    f = Wizard(draft.MemoryDraftStore())
    sub(f, "zone", {}); sub(f, "zone_name", {"name": "Hall"})
    sub(f, "zone_sensors", {"illuminance": "sensor.l", "presence": "binary_sensor.p"})
    assert {(s["role"], s["entity"]) for s in f.cfg["sensors"]} == {("illuminance", "sensor.l"), ("presence", "binary_sensor.p")}
    sub(f, "zone_sensors", {"presence": "binary_sensor.p"})
    assert {s["role"] for s in f.cfg["sensors"]} == {"presence"}


def test_a_zone_without_illuminance_reaches_the_save_step_without_errors():
    f = Wizard(draft.MemoryDraftStore())
    sub(f, "zone", {}); sub(f, "zone_name", {"name": "Hall"}); sub(f, "zone_sensors", {}); sub(f, "zone_lamps", {"lamps": ["light.h"]})
    r = sub(f, "save")
    assert r["type"] == "form" and r["step_id"] == "save" and not r["errors"]
    r = sub(f, "save", {})
    assert r["type"] == "create_entry" and r["data"]["config"]["zones"][0]["key"] == "hall"


# ---------------------------------------------------------------- areas

def test_area_step_offers_areas_and_prefills_name_key_lamps_and_sensors():
    f = Wizard(draft.MemoryDraftStore())
    r = sub(f, "zone")
    assert r["type"] == "form" and r["step_id"] == "zone"
    opts = r["data_schema"].schema[next(iter(r["data_schema"].schema))].config["options"]
    assert opts == [{"value": "living_room", "label": "Гостиная"}, {"value": "bedroom", "label": "Bedroom"}]
    r = sub(f, "zone", {"area": "bedroom"})
    assert r["step_id"] == "zone_name" and fields(r) == {"name": "Bedroom", "key": "bedroom"}
    r = sub(f, "zone_name", {"name": "Bedroom", "key": "bedroom"})
    assert r["step_id"] == "zone_sensors"
    assert fields(r) == {"illuminance": "sensor.bed_lux", "presence": ["binary_sensor.bed_occ"], "media": ["media_player.bed_tv"]}
    r = sub(f, "zone_sensors", {"illuminance": "sensor.bed_lux"})
    assert r["step_id"] == "zone_lamps" and fields(r) == {"lamps": ["light.bed_a", "light.bed_b"]}
    z = f.cfg["zones"][0]
    assert z["area_id"] == "bedroom" and z["key"] == "bedroom"


def test_custom_name_still_works_and_area_name_can_be_overridden():
    f = Wizard(draft.MemoryDraftStore())
    sub(f, "zone", {})                                                       # no area picked = custom zone
    r = sub(f, "zone_name", None)
    assert fields(r) == {"name": None, "key": None}
    sub(f, "zone_name", {"name": "Моя берлога"})
    assert f.cfg["zones"][0]["name"] == "Моя берлога" and f.cfg["zones"][0]["key"] == "moia_berloga" and "area_id" not in f.cfg["zones"][0]
    sub(f, "zone", {"area": "bedroom"}); sub(f, "zone_name", {"name": "Master bedroom", "key": ""})
    z = f.cfg["zones"][1]
    assert (z["name"], z["key"], z["area_id"]) == ("Master bedroom", "bedroom", "bedroom")
    assert fields(sub(f, "zone_sensors", None))["illuminance"] == "sensor.bed_lux"  # prefilled although the name was overridden


def test_zone_name_validation_and_used_areas_are_hidden():
    f = Wizard(draft.MemoryDraftStore())
    sub(f, "zone", {"area": "bedroom"})
    assert sub(f, "zone_name", {"name": "  "})["errors"] == {"base": "zone_needs_name"}
    assert sub(f, "zone_name", {"name": "X", "key": "Bad Key"})["errors"] == {"base": "bad_key"}
    sub(f, "zone_name", {"name": "Bedroom"})
    sub(f, "zone", {}); assert sub(f, "zone_name", {"name": "Other", "key": "bedroom"})["errors"] == {"base": "duplicate_key"}
    f2 = Wizard(draft.MemoryDraftStore()); sub(f2, "zone", {"area": "bedroom"}); sub(f2, "zone_name", {"name": "Bedroom"})
    r = sub(f2, "zone")
    assert [o["value"] for o in r["data_schema"].schema[next(iter(r["data_schema"].schema))].config["options"]] == ["living_room"]


def test_without_areas_the_zone_step_goes_straight_to_the_name():
    f = Wizard(draft.MemoryDraftStore(), areas=[])
    assert sub(f, "zone")["step_id"] == "zone_name"


def test_options_flow_new_zone_uses_areas_too():
    from adaptive_light.model import plan_living_room
    f = Options(plan_living_room())
    run(f.async_step_init())
    r = sub(f, "new_zone")
    assert r["step_id"] == "new_zone"
    sub(f, "new_zone", {"area": "bedroom"}); r = sub(f, "zone_name", {"name": "Bedroom"})
    assert r["step_id"] == "zone_sensors" and fields(r)["illuminance"] == "sensor.bed_lux"
    assert [z["key"] for z in f.cfg["zones"]] == ["living_room", "bedroom"]
    sub(f, "zone_sensors", {})
    assert sub(f, "zone_menu")["step_id"] == "zone_menu" and not f._errors()      # a zone without illuminance saves fine


# ---------------------------------------------------------------- draft

def test_every_step_saves_a_draft_and_finishing_removes_it():
    store = draft.MemoryDraftStore()
    f = Wizard(store)
    r = sub(f, "user"); assert [o for o in r["menu_options"]] == ["global_sensors", "preset_plan"]
    assert store.data is None
    sub(f, "global_sensors", {"radiation": "sensor.rad"}); assert store.data["step"] == "zone" and store.data["config"]["sensors"]
    sub(f, "zone", {}); sub(f, "zone_name", {"name": "Hall"}); assert store.data["step"] == "zone_sensors" and store.data["zone"] == "hall"
    sub(f, "zone_sensors", {"illuminance": "sensor.l"}); assert store.data["step"] == "zone_lamps"
    sub(f, "zone_lamps", {"lamps": ["light.h1", "light.h2"]}); assert store.data["step"] == "groups_menu"
    assert len(store.data["config"]["lamps"]) == 2
    sub(f, "group_add", {"name": "All", "lamps": ["h1", "h2"]}); assert store.data["step"] == "groups_menu"
    r = sub(f, "save", {})
    assert r["type"] == "create_entry" and store.data is None


def test_closing_the_dialog_and_coming_back_offers_to_resume_where_it_stopped():
    store = draft.MemoryDraftStore()
    f = Wizard(store)
    sub(f, "global_sensors", {}); sub(f, "zone", {}); sub(f, "zone_name", {"name": "Hall"})
    sub(f, "zone_sensors", {"illuminance": "sensor.l"}); sub(f, "zone_lamps", {"lamps": ["light.h1"]})
    saved = store.data["config"]
    g = Wizard(store, now=2000.0)                                            # a fresh flow = the dialog was reopened
    r = sub(g, "user")
    assert r["menu_options"] == ["resume_draft", "global_sensors", "preset_plan", "discard_draft"]
    assert "Hall" in r["description_placeholders"]["draft"] and "1 lamp" in r["description_placeholders"]["draft"]
    r = sub(g, "resume_draft")
    assert r["type"] == "menu" and r["step_id"] == "groups_menu" and g.cfg == saved and g._zone == "hall"
    sub(g, "group_add", {"name": "G", "lamps": ["h1"]})
    assert sub(g, "save", {})["type"] == "create_entry" and store.data is None


def test_resume_from_the_middle_of_a_zone_form_and_russian_note():
    store = draft.MemoryDraftStore()
    f = Wizard(store)
    sub(f, "global_sensors", {}); sub(f, "zone", {"area": "bedroom"}); sub(f, "zone_name", {"name": "Bedroom"})   # stopped at zone_sensors
    g = Wizard(store, now=1500.0); g.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="ru"))
    assert "Найдена незавершённая настройка" in sub(g, "user")["description_placeholders"]["draft"]
    r = sub(g, "resume_draft")
    assert r["step_id"] == "zone_sensors" and fields(r)["illuminance"] == "sensor.bed_lux"        # area prefill survives the resume


def test_discard_and_expired_or_broken_drafts():
    store = draft.MemoryDraftStore()
    f = Wizard(store); sub(f, "global_sensors", {"radiation": "sensor.r"})
    g = Wizard(store); r = sub(g, "discard_draft")
    assert store.data is None and r["menu_options"] == ["global_sensors", "preset_plan"]
    sub(f, "global_sensors", {"radiation": "sensor.r"})
    old = Wizard(store, now=1000.0 + draft.DRAFT_MAX_AGE_S + 5)
    assert sub(old, "user")["menu_options"] == ["global_sensors", "preset_plan"]                  # expired draft is ignored
    store.data = {"version": 1, "ts": 1000.0, "config": {"zones": "garbage"}}
    assert sub(Wizard(store), "user")["menu_options"] == ["global_sensors", "preset_plan"]
    assert sub(Wizard(store), "resume_draft")["step_id"] == "user"                                # resume with nothing to resume


def test_a_failing_draft_store_does_not_break_the_wizard():
    class Broken(draft.MemoryDraftStore):
        async def async_save(self, data): raise OSError("disk full")
    f = Wizard(Broken())
    sub(f, "global_sensors", {"radiation": "sensor.r"})
    r = sub(f, "zone", {}); assert r["step_id"] == "zone_name"


def test_preset_goes_to_save_and_clears_its_draft():
    store = draft.MemoryDraftStore(); f = Wizard(store)
    r = sub(f, "preset_plan"); assert r["step_id"] == "save" and store.data["step"] == "save"
    assert sub(f, "save", {})["type"] == "create_entry" and store.data is None


def test_zone_sensors_keep_one_presence_sensor_and_accept_several():
    from adaptive_light.model import plan_living_room
    f = Options(plan_living_room())
    f._load()
    f._zone = "living_room"
    lux = "sensor.living_room_presence_sensor_light_sensor_light_level"
    one = "binary_sensor.living_room_presence_sensor"
    r = sub(f, "zone_sensors")
    assert fields(r)["presence"] == [one]
    sub(f, "zone_sensors", {"illuminance": lux, "presence": [one]})
    got = [s["entity"] for s in f.cfg["sensors"] if s["role"] == "presence" and s["zone"] == "living_room"]
    assert got == [one]
    sub(f, "zone_sensors", {"illuminance": lux, "presence": [one, "binary_sensor.living_room_presence_sensor_entrance_zone"]})
    got = [s["entity"] for s in f.cfg["sensors"] if s["role"] == "presence" and s["zone"] == "living_room"]
    assert got == [one, "binary_sensor.living_room_presence_sensor_entrance_zone"]



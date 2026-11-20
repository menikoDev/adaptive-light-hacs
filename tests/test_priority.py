"""A shared lamp follows the active zone with the higher priority, then the smaller key."""
from adaptive_light.const import DOMAIN
from adaptive_light.manager import AdaptiveLightManager
from adaptive_light.model import normalise

from test_events import _logger, _stop
from test_presence import Home, run


def _room(**extra):
    zone = {
        "key": "living_room",
        "name": "Living Room",
        "lamps": ["light.ceiling", "light.fridge"],
        "presence": ["binary_sensor.room"],
        "off_timeout": 30,
        "threshold": 50,
        "window": 10,
        "priority": 0,
    }
    zone.update(extra)
    return zone


def _fridge(**extra):
    zone = {
        "key": "fridge",
        "name": "Fridge",
        "lamps": ["light.fridge"],
        "presence": ["binary_sensor.door"],
        "off_timeout": 10,
        "threshold": 0,
        "priority": 10,
    }
    zone.update(extra)
    return zone


def _mgr(home, zones):
    return AdaptiveLightManager(home, {"version": 5, "zones": zones}, now=home.now)


def _presence(home, room="off", door="off"):
    home.states.set("binary_sensor.room", room)
    home.states.set("binary_sensor.door", door)
    home.states.set("light.ceiling", "off")
    home.states.set("light.fridge", "off")


def _calls(home, entity):
    return [call for call in home.services.calls if entity in call[2].get("entity_id", [])]


def test_missing_empty_and_negative_priority_read_as_zero_and_color_stays_empty():
    cfg = normalise({"version": 5, "zones": [
        {"key": "hall", "name": "Hall", "lamps": ["light.a"], "presence": ["binary_sensor.p"], "off_timeout": 10},
        {"key": "kitchen", "name": "Kitchen", "lamps": ["light.b"], "presence": ["binary_sensor.q"], "off_timeout": 10, "priority": ""},
        {"key": "study", "name": "Study", "lamps": ["light.c"], "presence": ["binary_sensor.r"], "off_timeout": 10, "priority": -4},
    ]})
    assert [zone["priority"] for zone in cfg["zones"]] == [0, 0, 0]
    assert all(
        zone["color"] == "" and zone["brightness"] is None
        and zone["hold_color"] is False and zone["hold_brightness"] is False
        for zone in cfg["zones"]
    )


def test_activation_gives_the_shared_lamp_to_the_higher_zone():
    home = Home()
    _presence(home, room="on", door="off")
    mgr = _mgr(home, [_room(), _fridge(color="ice", brightness=20, hold_color=True, hold_brightness=True)])
    log, cap = _logger()
    try:
        assert run(mgr.apply("living_room")) == "on"
        home.states.set("light.ceiling", "on")
        home.states.set("light.fridge", "on")
        home.services.calls.clear()
        home.states.set("binary_sensor.door", "on")
        home.states.set("light.fridge", "off")
        run(mgr.apply("living_room"))
    finally:
        _stop(log, cap)
    shared = _calls(home, "light.fridge")
    assert shared == [
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice", "brightness": 20}),
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice"}),
    ]
    assert all("light.fridge" not in call[2]["entity_id"] or "color_name" in call[2] for call in home.services.calls)
    assert any("reason=owner" in line and "entity=light.fridge" in line and "old=living_room" in line and "new=fridge" in line for line in cap.lines)


def test_deactivation_hands_the_lamp_back_immediately_with_entity_id_only():
    home = Home()
    _presence(home, room="on", door="on")
    home.states.set("light.ceiling", "on")
    home.states.set("light.fridge", "on")
    mgr = _mgr(home, [_room(), _fridge(color="ice", brightness=20)])
    run(mgr.apply("fridge"))
    home.services.calls.clear()

    home.states.set("binary_sensor.door", "off")
    home.t = 100
    assert run(mgr.apply("fridge")) == "wait"
    home.t = 109
    home.states.set("light.fridge", "off")
    assert run(mgr.apply("fridge")) == "wait"
    assert _calls(home, "light.fridge") == []

    home.t = 110
    run(mgr.apply("fridge"))
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.fridge"]})]
    assert set(home.services.calls[0][2]) == {"entity_id"}


def test_equal_priority_uses_the_smaller_key():
    home = Home()
    home.states.set("binary_sensor.a", "on")
    home.states.set("binary_sensor.b", "on")
    home.states.set("light.shared", "off")
    zones = [
        {"key": "beta", "name": "Beta", "lamps": ["light.shared"], "presence": ["binary_sensor.b"], "off_timeout": 10, "priority": 10, "color": "red", "brightness": 10},
        {"key": "alpha", "name": "Alpha", "lamps": ["light.shared"], "presence": ["binary_sensor.a"], "off_timeout": 10, "priority": 10},
    ]
    mgr = _mgr(home, zones)
    run(mgr.apply("beta"))
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.shared"]})]

    home.services.calls.clear()
    home.states.set("light.shared", "off")
    rooms = [
        {"key": "study", "name": "Study", "lamps": ["light.shared"], "presence": ["binary_sensor.b"], "off_timeout": 10, "priority": 0, "color": "red"},
        {"key": "living_room", "name": "Living Room", "lamps": ["light.shared"], "presence": ["binary_sensor.a"], "off_timeout": 10, "priority": 0},
    ]
    other = _mgr(home, rooms)
    run(other.apply("study"))
    assert home.services.calls == [("light", "turn_on", {"entity_id": ["light.shared"]})]


def test_color_and_brightness_are_sent_only_when_the_zone_set_them():
    home = Home()
    _presence(home, room="off", door="on")
    mgr = _mgr(home, [_room(), _fridge(color="ice", brightness=20, hold_color=True, hold_brightness=True)])
    run(mgr.apply("fridge"))
    assert home.services.calls == [
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice", "brightness": 20}),
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice"}),
    ]

    home.services.calls.clear()
    home.states.set("light.fridge", "on")
    run(mgr.apply("fridge"))
    assert home.services.calls == [
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice", "brightness": 20}),
        ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice"}),
    ]

    home.services.calls.clear()
    home.states.set("binary_sensor.door", "off")
    home.states.set("binary_sensor.room", "on")
    home.states.set("light.ceiling", "off")
    home.states.set("light.fridge", "off")
    plain = _mgr(home, [_room()])
    run(plain.apply("living_room"))
    assert plain.hass.services.calls[-1] == ("light", "turn_on", {"entity_id": ["light.ceiling", "light.fridge"]})
    assert set(home.services.calls[-1][2]) == {"entity_id"}
    home.states.set("light.ceiling", "on")
    home.states.set("light.fridge", "on")
    home.services.calls.clear()
    run(plain.apply("living_room"))
    assert home.services.calls == []


def test_one_shared_lamp_does_not_take_the_rest_of_the_room():
    home = Home()
    _presence(home, room="on", door="on")
    mgr = _mgr(home, [_room(), _fridge(color="ice", hold_color=True)])
    run(mgr.apply("living_room"))
    assert ("light", "turn_on", {"entity_id": ["light.ceiling"]}) in home.services.calls
    assert ("light", "turn_on", {"entity_id": ["light.fridge"], "color_name": "ice"}) in home.services.calls
    assert not any(call[2].get("entity_id") == ["light.ceiling", "light.fridge"] for call in home.services.calls)


def test_a_paused_higher_zone_releases_the_lamp_immediately():
    home = Home()
    _presence(home, room="on", door="on")
    mgr = _mgr(home, [_room(), _fridge(color="ice", brightness=20)])
    mgr.enabled["fridge"] = False
    run(mgr.apply("fridge"))
    turned = [call for call in home.services.calls if call[1] == "turn_on"]
    assert turned
    assert all("color_name" not in call[2] and "brightness" not in call[2] for call in turned)
    assert any("light.fridge" in call[2]["entity_id"] for call in turned)


def test_threshold_zero_on_the_higher_zone_stays_presence_only():
    home = Home()
    _presence(home, room="on", door="on")
    home.states.set("sensor.room", "10000")
    mgr = _mgr(home, [
        _room(lux="sensor.room", threshold=50, window=10),
        _fridge(lux="sensor.room", threshold=0),
    ])
    run(mgr.apply("living_room"))
    assert _calls(home, "light.fridge") == [("light", "turn_on", {"entity_id": ["light.fridge"]})]
    assert _calls(home, "light.ceiling") == []


def test_our_turn_off_does_not_stick_and_an_external_turn_off_blocks_both_zones():
    home = Home()
    _presence(home, room="on", door="on")
    mgr = _mgr(home, [_room(), _fridge()])
    run(mgr.apply("fridge"))
    home.states.set("light.fridge", "on")
    mgr.note_lamp_change("light.fridge", "on")
    home.states.set("binary_sensor.room", "off")
    home.states.set("binary_sensor.door", "off")
    home.t = 0
    run(mgr.apply("fridge"))
    home.t = 10
    home.services.calls.clear()
    assert run(mgr.apply("fridge")) == "off"
    assert ("light", "turn_off", {"entity_id": ["light.fridge"]}) in home.services.calls
    home.states.set("light.fridge", "off")
    mgr.note_lamp_change("light.fridge", "off")
    assert mgr.manual_off == set()

    home.states.set("binary_sensor.room", "on")
    home.states.set("binary_sensor.door", "on")
    home.services.calls.clear()
    run(mgr.apply("fridge"))
    assert any(call[1] == "turn_on" and "light.fridge" in call[2]["entity_id"] for call in home.services.calls)

    home.states.set("light.fridge", "on")
    mgr.note_lamp_change("light.fridge", "on")
    home.states.set("light.fridge", "off")
    mgr.note_lamp_change("light.fridge", "off")
    assert "light.fridge" in mgr.manual_off
    home.services.calls.clear()
    mgr.enabled["fridge"] = False
    run(mgr.apply("living_room"))
    assert _calls(home, "light.fridge") == []


def test_a_turn_off_from_another_entry_does_not_stick_and_an_external_off_blocks_both():
    home = Home()
    home.states.set("binary_sensor.room", "off")
    home.states.set("binary_sensor.door", "off")
    home.states.set("light.fridge", "on")
    room = _mgr(home, [_room(lamps=["light.fridge"])])
    fridge = _mgr(home, [_fridge()])
    home.data = {DOMAIN: {"room": room, "fridge": fridge}}

    home.t = 0
    run(fridge.apply("fridge"))
    home.t = 10
    run(fridge.apply("fridge"))
    assert ("light", "turn_off", {"entity_id": ["light.fridge"]}) in home.services.calls
    home.states.set("light.fridge", "off")
    room.note_lamp_change("light.fridge", "off")
    assert room.manual_off == set() and fridge.manual_off == set()

    home.states.set("light.fridge", "on")
    room.note_lamp_change("light.fridge", "on")
    fridge.note_lamp_change("light.fridge", "on")
    home.states.set("light.fridge", "off")
    room.note_lamp_change("light.fridge", "off")
    assert "light.fridge" in room.manual_off and "light.fridge" in fridge.manual_off

    home.states.set("binary_sensor.room", "on")
    home.states.set("binary_sensor.door", "on")
    home.services.calls.clear()
    run(fridge.apply("fridge"))
    fridge.enabled["fridge"] = False
    run(room.apply("living_room"))
    assert _calls(home, "light.fridge") == []


def test_an_idle_zone_turns_off_a_shared_lamp_handed_back():
    """The fridge times out, the living room owns the lamp, then times out too.

    Ownership falls back to the fridge, which is already idle. It must turn the lamp off.
    """
    home = Home()
    lamp = "light.living_room_top_lamp_bottom"
    home.states.set("binary_sensor.room", "off")
    home.states.set("binary_sensor.door", "on")
    home.states.set(lamp, "off")
    mgr = _mgr(home, [
        _room(key="living_room_zone", lamps=[lamp], off_timeout=20, threshold=0, priority=0),
        _fridge(key="living_room_refrigerator", lamps=[lamp], off_timeout=14, threshold=0, priority=10),
    ])
    assert run(mgr.apply("living_room_refrigerator")) == "on"
    assert mgr._owner_of(lamp)[1] == "living_room_refrigerator"
    home.states.set(lamp, "on")

    home.t = 6
    home.states.set("binary_sensor.door", "off")
    assert run(mgr.apply("living_room_refrigerator")) == "wait"
    assert mgr.is_active("living_room_refrigerator")

    home.states.set("binary_sensor.room", "on")
    home.t = 20
    assert run(mgr.apply("living_room_refrigerator")) == "off"
    assert mgr._owner_of(lamp)[1] == "living_room_zone"
    assert not mgr.is_active("living_room_refrigerator")
    home.states.set(lamp, "on")

    home.t = 20
    home.states.set("binary_sensor.room", "off")
    assert run(mgr.apply("living_room_zone")) == "wait"
    home.services.calls.clear()
    home.t = 40
    assert run(mgr.apply("living_room_zone")) == "off"
    assert mgr._owner_of(lamp)[1] == "living_room_refrigerator"
    assert not mgr.occupied("living_room_refrigerator")
    assert not mgr.is_active("living_room_refrigerator")
    assert ("light", "turn_off", {"entity_id": [lamp]}) in home.services.calls


def test_an_idle_higher_zone_does_not_steal_a_shared_lamp_when_lux_changes():
    home = Home()
    lamp = "light.living_room_top_lamp_bottom"
    _presence(home, room="on", door="off")
    home.states.set("light.ceiling", "on")
    home.states.set(lamp, "on")
    home.states.set("sensor.fridge", "40")
    mgr = _mgr(home, [
        _room(lamps=["light.ceiling", lamp]),
        _fridge(lamps=[lamp], lux="sensor.fridge", threshold=50, off_timeout=10),
    ])
    home.t = 0
    run(mgr.apply("living_room"))
    run(mgr.apply("fridge"))
    home.t = 10
    run(mgr.apply("fridge"))
    assert mgr._owner_of(lamp)[1] == "living_room"
    assert not mgr.is_active("fridge")
    home.services.calls.clear()
    home.t = 11
    home.states.set("sensor.fridge", "0")
    log, cap = _logger()
    try:
        assert run(mgr.apply("fridge")) == "wait"
    finally:
        _stop(log, cap)
    assert mgr._owner_of(lamp)[1] == "living_room"
    assert not mgr.is_active("fridge")
    assert _calls(home, lamp) == []
    assert not any("reason=owner" in line and "new=fridge" in line for line in cap.lines)


def test_releasing_the_beam_returns_the_ceiling_lamp_to_the_living_room_color():
    """The table zone holds blue. After it lets go, the living room does not keep that RGB.

    work_station_zone is the desk and does not own this ceiling lamp.
    """
    home = Home()
    home.services._available.add(("adaptive_lighting", "set_manual_control"))
    top_left = "light.living_room_top_lamp_top_left"
    top_right = "light.living_room_top_lamp_top_right"
    bottom = "light.living_room_top_lamp_bottom"
    blue = [0, 0, 255]
    home.states.set("binary_sensor.room", "on")
    home.states.set("binary_sensor.table", "on")
    home.states.set("binary_sensor.desk", "on")
    for lamp in (top_left, top_right, bottom, "light.desk"):
        home.states.set(lamp, "off")
    mgr = _mgr(home, [
        {
            "key": "living_room_zone",
            "name": "Living Room",
            "lamps": [top_left, top_right, bottom],
            "presence": ["binary_sensor.room"],
            "off_timeout": 30,
            "threshold": 0,
            "window": 10,
            "priority": 0,
            "color": "",
            "hold_color": False,
            "hold_brightness": False,
        },
        {
            "key": "light_beem_for_work_station",
            "name": "Light Beem For Work Station",
            "lamps": [top_left],
            "presence": ["binary_sensor.table"],
            "off_timeout": 10,
            "threshold": 0,
            "window": 10,
            "priority": 1,
            "color": blue,
            "hold_color": True,
        },
        {
            "key": "work_station_zone",
            "name": "Work Station",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.desk"],
            "off_timeout": 30,
            "threshold": 0,
            "priority": 2,
            "color": [255, 0, 0],
            "hold_color": True,
        },
    ])
    run(mgr.apply("living_room_zone"))
    run(mgr.apply("light_beem_for_work_station"))
    assert mgr._owner_of(top_left)[1] == "light_beem_for_work_station"
    assert ("adaptive_lighting", "set_manual_control", {"lights": [top_left], "manual_control": "color"}) in home.services.calls
    assert any(call[2].get("rgb_color") == blue for call in _calls(home, top_left))
    home.states.set(top_left, "on")

    home.t = 0
    home.states.set("binary_sensor.table", "off")
    assert run(mgr.apply("light_beem_for_work_station")) == "wait"
    assert mgr._owner_of(top_left)[1] == "light_beem_for_work_station"
    home.services.calls.clear()
    home.t = 10
    run(mgr.apply("light_beem_for_work_station"))
    assert mgr._owner_of(top_left)[1] == "living_room_zone"
    assert mgr._owner_of("light.desk")[1] == "work_station_zone"
    assert ("adaptive_lighting", "set_manual_control", {"lights": [top_left], "manual_control": False}) in home.services.calls
    assert all(call[2].get("rgb_color") != blue for call in _calls(home, top_left))
    assert all("rgb_color" not in call[2] and "color_temp_kelvin" not in call[2] and "color_name" not in call[2] for call in _calls(home, top_left))

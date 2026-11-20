"""The event log records state changes and lamp on/off lines."""
import logging
import types

from adaptive_light.events import LOGGER_NAME
from adaptive_light.manager import AdaptiveLightManager

from test_presence import Home, run


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _logger():
    log = logging.getLogger(LOGGER_NAME)
    cap = _Capture()
    log.addHandler(cap)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log, cap


def _stop(log, cap):
    log.removeHandler(cap)
    log.propagate = True


def _zone(**extra):
    zone = {
        "key": "living_room",
        "name": "Living Room",
        "lamps": ["light.ceiling_1", "light.ceiling_2", "light.ceiling_3"],
        "presence": ["binary_sensor.motion_a", "binary_sensor.motion_b"],
        "off_timeout": 300,
        "lux": "sensor.living_room_lux",
        "threshold": 50,
        "window": 10,
    }
    zone.update(extra)
    return {"version": 5, "zones": [zone]}


def _ready(home, **extra):
    home.states.set("binary_sensor.motion_a", "on")
    home.states.set("binary_sensor.motion_b", "off")
    home.states.set("sensor.living_room_lux", "40")
    for lamp in ("light.ceiling_1", "light.ceiling_2", "light.ceiling_3"):
        home.states.set(lamp, "off")
    return AdaptiveLightManager(home, _zone(**extra), now=home.now)


def test_exporter_records_a_state_change_and_a_lamp_event():
    home = Home()
    mgr = _ready(home)
    log, cap = _logger()
    try:
        assert run(mgr.apply("living_room")) == "on"
    finally:
        _stop(log, cap)
    text = "\n".join(cap.lines)
    occupied = [line for line in cap.lines if "reason=occupied" in line]
    lamps = [line for line in cap.lines if "entity=light.ceiling_1" in line and "reason=average-below" in line]
    assert occupied and "new=on" in occupied[0] and "zone=living_room" in occupied[0]
    assert "ts=1970-01-01T00:00:00Z" in occupied[0]
    assert lamps and "old=off" in lamps[0] and "new=on" in lamps[0]
    assert "reason=estimate" in text and "new=40" in text
    assert "reason=thresholds" in text and "threshold=50,window=10" in text
    assert home.services.calls == [(
        "light", "turn_on",
        {"entity_id": ["light.ceiling_1", "light.ceiling_2", "light.ceiling_3"]},
    )]
    assert set(home.services.calls[0][2]) == {"entity_id"}


def test_a_low_average_stays_on_and_a_rise_above_the_threshold_is_logged():
    home = Home()
    mgr = _ready(home, window=1)
    log, cap = _logger()
    try:
        assert run(mgr.apply("living_room")) == "on"
        for lamp in ("light.ceiling_1", "light.ceiling_2", "light.ceiling_3"):
            home.states.set(lamp, "on")
        home.t = 30
        home.states.set("sensor.living_room_lux", "0")
        assert run(mgr.apply("living_room")) == "on"
        home.t = 31
        home.states.set("sensor.living_room_lux", "400")
        run(mgr.apply("living_room"))
        home.t = 120
        assert run(mgr.apply("living_room")) == "off"
    finally:
        _stop(log, cap)
    assert any("reason=average-below" in line and "new=on" in line for line in cap.lines)
    assert any("reason=average-above" in line and "entity=living_room" in line and "new=off" in line for line in cap.lines)
    assert not any("reason=average-below-lower" in line for line in cap.lines)


def test_kelvin_is_sent_and_logged_and_an_empty_color_stays_entity_id_only():
    home = Home()
    home.states.set("binary_sensor.p", "on")
    home.states.set("sensor.living_room_lux", "40")
    home.states.set("light.top", "off")
    colored = {
        "version": 5,
        "zones": [{
            "key": "living_room",
            "name": "Living Room",
            "lamps": ["light.top"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "lux": "sensor.living_room_lux",
            "threshold": 50,
            "window": 10,
            "color": "",
            "color_temp": 2700,
            "hold_color": True,
        }],
    }
    mgr = AdaptiveLightManager(home, colored, now=home.now)
    log, cap = _logger()
    try:
        assert run(mgr.apply("living_room")) == "on"
        home.states.set("light.top", "on")
        run(mgr.apply("living_room"))
    finally:
        _stop(log, cap)
    assert home.services.calls[0] == ("light", "turn_on", {"entity_id": ["light.top"], "color_temp_kelvin": 2700})
    assert home.services.calls[1] == ("light", "turn_on", {"entity_id": ["light.top"], "color_temp_kelvin": 2700})
    stuck = next(line for line in cap.lines if "entity=light.top" in line and "old=on" in line and "new=on" in line)
    assert "reason=average-below" in stuck
    assert "service=turn_on" in stuck and "kelvin=2700" in stuck and "rgb=-" in stuck
    assert "brightness=-" in stuck and "presence=on" in stuck and "estimate=40" in stuck
    assert "threshold=50" in stuck and "window=10" in stuck and "owner=living_room" in stuck

    plain_home = Home()
    plain_home.states.set("binary_sensor.p", "on")
    plain_home.states.set("light.top", "off")
    plain = AdaptiveLightManager(plain_home, {
        "version": 5,
        "zones": [{
            "key": "living_room",
            "name": "Living Room",
            "lamps": ["light.top"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
        }],
    }, now=plain_home.now)
    run(plain.apply("living_room"))
    assert plain_home.services.calls == [("light", "turn_on", {"entity_id": ["light.top"]})]


def test_threshold_zero_sends_kelvin_and_holds_it_after_another_integration_repaints():
    """A presence-only zone still sends kelvin, then sends it again if the lamp is painted white."""
    home = Home()
    home.services._available.add(("adaptive_lighting", "set_manual_control"))
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.desk", "off")
    mgr = AdaptiveLightManager(home, {
        "version": 5,
        "zones": [{
            "key": "desk",
            "name": "Light Beem For Work Station",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "window": 10,
            "color": "",
            "color_temp": 4003,
            "brightness": 246,
            "hold_color": True,
            "hold_brightness": True,
        }],
    }, now=home.now)
    assert run(mgr.apply("desk")) == "on"
    assert home.services.calls[:3] == [
        ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": True}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "color_temp_kelvin": 4003, "brightness": 246}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "color_temp_kelvin": 4003}),
    ]
    home.states.set("light.desk", "on", attributes={
        "color_temp_kelvin": 6500, "rgb_color": (255, 255, 255), "brightness": 255,
    })
    run(mgr._on_event(types.SimpleNamespace(data={
        "entity_id": "light.desk",
        "new_state": home.states.get("light.desk"),
    })))
    assert home.services.calls[-2:] == [
        ("light", "turn_on", {"entity_id": ["light.desk"], "color_temp_kelvin": 4003, "brightness": 246}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "color_temp_kelvin": 4003}),
    ]
    assert any(call[1] == "set_manual_control" and call[2]["manual_control"] is True for call in home.services.calls[3:])


def test_hold_color_and_hold_brightness_call_adaptive_lighting_for_that_part_only():
    """Checked sends that part and marks it manual. Unchecked sends nothing. Empty color does not call Adaptive Lighting."""
    def zone(**extra):
        base = {
            "key": "desk",
            "name": "Light Beem For Work Station",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "color": [20, 180, 60],
            "color_temp": 4003,
            "brightness": 246,
        }
        base.update(extra)
        return base

    def run_zone(**extra):
        home = Home()
        home.services._available.add(("adaptive_lighting", "set_manual_control"))
        home.states.set("binary_sensor.p", "on")
        home.states.set("light.desk", "off")
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [zone(**extra)]}, now=home.now)
        assert run(mgr.apply("desk")) == "on"
        return home

    color_only = run_zone(hold_color=True, hold_brightness=False)
    assert color_only.services.calls == [
        ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": "color"}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "rgb_color": [20, 180, 60]}),
    ]

    kelvin_only = run_zone(hold_color=True, hold_brightness=False, color="")
    assert kelvin_only.services.calls == [
        ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": "color"}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "color_temp_kelvin": 4003}),
    ]

    bright_only = run_zone(hold_color=False, hold_brightness=True)
    assert bright_only.services.calls == [
        ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": "brightness"}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "brightness": 246}),
    ]

    both = run_zone(hold_color=True, hold_brightness=True)
    assert both.services.calls[:3] == [
        ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": True}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "rgb_color": [20, 180, 60], "brightness": 246}),
        ("light", "turn_on", {"entity_id": ["light.desk"], "rgb_color": [20, 180, 60]}),
    ]

    neither = run_zone(hold_color=False, hold_brightness=False)
    assert neither.services.calls == [("light", "turn_on", {"entity_id": ["light.desk"]})]

    empty = run_zone(hold_color=True, hold_brightness=False, color="", color_temp=None)
    assert empty.services.calls == [("light", "turn_on", {"entity_id": ["light.desk"]})]
    assert all(call[0] != "adaptive_lighting" for call in empty.services.calls)


def test_hold_color_with_rgb_green_turns_the_lamp_green():
    """Red 0, green 255, blue 0 is the color he types. Kelvin on the same zone does not replace it."""
    from adaptive_light.model import rgb_channels

    assert rgb_channels(0, 255, 0) == [0, 255, 0]
    assert rgb_channels(None, 255, None) == [0, 255, 0]
    assert rgb_channels("", "", "") == ""
    home = Home()
    home.services._available.add(("adaptive_lighting", "set_manual_control"))
    home.states.set("binary_sensor.p", "on")
    home.states.set("light.desk", "off")
    mgr = AdaptiveLightManager(home, {
        "version": 5,
        "zones": [{
            "key": "desk",
            "name": "Light Beem For Work Station",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "color": rgb_channels(0, 255, 0),
            "color_temp": 4003,
            "hold_color": True,
        }],
    }, now=home.now)
    assert run(mgr.apply("desk")) == "on"
    assert ("adaptive_lighting", "set_manual_control", {"lights": ["light.desk"], "manual_control": "color"}) in home.services.calls
    turned = [call for call in home.services.calls if call[0] == "light" and call[1] == "turn_on"]
    assert turned
    assert all(call[2].get("rgb_color") == [0, 255, 0] for call in turned)
    assert all("color_temp_kelvin" not in call[2] for call in turned)


def test_outside_the_active_window_an_occupied_zone_does_not_turn_the_lamp_on():
    """Presence is on, but 20:15 is outside 09:00–17:00, so the lamp stays off.

    Equal or empty hours stay always active. A higher zone outside its hours
    does not take a shared lamp from a zone that is inside its hours.
    """
    from datetime import datetime

    def at(hour, minute):
        return lambda: datetime(2026, 11, 20, hour, minute)

    def one(hour, minute, **extra):
        home = Home()
        home.states.set("binary_sensor.p", "on")
        home.states.set("light.desk", "off")
        zone = {
            "key": "desk",
            "name": "Desk",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "active_hours": True,
            "active_start": "09:00",
            "active_end": "17:00",
        }
        zone.update(extra)
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [zone]}, now=home.now, local_now=at(hour, minute))
        return home, run(mgr.apply("desk"))

    home, result = one(20, 15)
    assert result == "wait"
    assert home.services.calls == []

    home, result = one(12, 0)
    assert result == "on"
    assert ("light", "turn_on", {"entity_id": ["light.desk"]}) in home.services.calls

    home, result = one(20, 15, active_start="09:00", active_end="09:00")
    assert result == "on"
    home, result = one(20, 15, active_start="", active_end="")
    assert result == "on"

    home = Home()
    lamp = "light.living_room_top_lamp_top_left"
    home.states.set("binary_sensor.room", "on")
    home.states.set("binary_sensor.table", "on")
    home.states.set(lamp, "off")
    mgr = AdaptiveLightManager(home, {"version": 5, "zones": [
        {
            "key": "living_room_zone",
            "name": "Living Room",
            "lamps": [lamp],
            "presence": ["binary_sensor.room"],
            "off_timeout": 30,
            "threshold": 0,
            "priority": 0,
        },
        {
            "key": "light_beem_for_work_station",
            "name": "Light Beem For Work Station",
            "lamps": [lamp],
            "presence": ["binary_sensor.table"],
            "off_timeout": 30,
            "threshold": 0,
            "priority": 1,
            "color": [0, 0, 255],
            "hold_color": True,
            "active_hours": True,
            "active_start": "09:00",
            "active_end": "17:00",
        },
    ]}, now=home.now, local_now=at(20, 15))
    assert run(mgr.apply("light_beem_for_work_station")) == "wait"
    assert mgr._owner_of(lamp)[1] == "living_room_zone"
    assert all(call[2].get("rgb_color") != [0, 0, 255] for call in home.services.calls)


def test_active_hours_switch_off_ignores_a_filled_overnight_window():
    """From 15:00 until 10:00 crosses midnight. The switch defaults off, so filled times do nothing."""
    from datetime import datetime

    def at(hour, minute):
        return lambda: datetime(2026, 11, 20, hour, minute)

    def apply_at(hour, minute, **extra):
        home = Home()
        home.states.set("binary_sensor.p", "on")
        home.states.set("light.desk", "off")
        zone = {
            "key": "desk",
            "name": "Desk",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "active_start": "15:00",
            "active_end": "10:00",
        }
        zone.update(extra)
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [zone]}, now=home.now, local_now=at(hour, minute))
        return run(mgr.apply("desk"))

    assert apply_at(12, 0) == "on"
    assert apply_at(12, 0, active_hours=False) == "on"
    assert apply_at(12, 0, active_hours=True) == "wait"
    assert apply_at(15, 0, active_hours=True) == "on"
    assert apply_at(9, 30, active_hours=True) == "on"
    assert apply_at(10, 0, active_hours=True) == "wait"


def test_two_windows_the_second_one_turns_the_lamp_on_and_takes_a_shared_lamp():
    """19:00 sits in 18:00–20:00 and not in 09:00–10:00. That second window is enough."""
    from datetime import datetime

    def at(hour, minute):
        return lambda: datetime(2026, 11, 20, hour, minute)

    windows = [{"start": "09:00", "end": "10:00"}, {"start": "18:00", "end": "20:00"}]

    def one(hour, minute, **extra):
        home = Home()
        home.states.set("binary_sensor.p", "on")
        home.states.set("light.desk", "off")
        zone = {
            "key": "desk",
            "name": "Desk",
            "lamps": ["light.desk"],
            "presence": ["binary_sensor.p"],
            "off_timeout": 30,
            "threshold": 0,
            "active_hours": True,
            "active_windows": windows,
        }
        zone.update(extra)
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [zone]}, now=home.now, local_now=at(hour, minute))
        return home, mgr, run(mgr.apply("desk"))

    home, mgr, result = one(19, 0)
    assert result == "on"
    assert mgr.cfg.zones["desk"]["active_windows"] == windows
    assert ("light", "turn_on", {"entity_id": ["light.desk"]}) in home.services.calls
    assert one(9, 30)[2] == "on"
    home, _mgr, result = one(12, 0)
    assert result == "wait"
    assert home.services.calls == []
    assert one(12, 0, active_hours=False)[2] == "on"
    assert one(12, 0, active_windows=[])[2] == "on"
    overnight = [{"start": "09:00", "end": "10:00"}, {"start": "22:00", "end": "06:00"}]
    assert one(23, 30, active_windows=overnight)[2] == "on"
    assert one(12, 0, active_windows=overnight)[2] == "wait"

    lamp = "light.shared"
    def shared(hour, minute):
        home = Home()
        home.states.set("binary_sensor.room", "on")
        home.states.set("binary_sensor.desk", "on")
        home.states.set(lamp, "off")
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [
            {
                "key": "living",
                "name": "Living",
                "lamps": [lamp],
                "presence": ["binary_sensor.room"],
                "off_timeout": 30,
                "threshold": 0,
                "priority": 0,
            },
            {
                "key": "desk",
                "name": "Desk",
                "lamps": [lamp],
                "presence": ["binary_sensor.desk"],
                "off_timeout": 30,
                "threshold": 0,
                "priority": 1,
                "active_hours": True,
                "active_windows": windows,
            },
        ]}, now=home.now, local_now=at(hour, minute))
        return mgr

    inside = shared(19, 0)
    assert run(inside.apply("desk")) == "on"
    assert inside._owner_of(lamp)[1] == "desk"
    outside = shared(12, 0)
    assert run(outside.apply("desk")) == "wait"
    assert outside._owner_of(lamp)[1] == "living"


def test_two_from_to_windows_match_the_string_windows():
    """Group From/To pairs sort by start and behave like the old HH:MM-HH:MM list."""
    from datetime import datetime

    from adaptive_light.model import windows_from_numbered, windows_input, zone_windows

    strings, bad = windows_input(["18:00-20:00", "09:00-10:00"])
    assert bad is False
    pairs, failed = windows_from_numbered([
        (1, "18:00:00", "20:00:00"),
        (2, "09:00", "10:00"),
    ])
    assert failed is None
    assert pairs == strings
    assert pairs == [{"start": "09:00", "end": "10:00"}, {"start": "18:00", "end": "20:00"}]
    overnight, failed = windows_from_numbered([(1, "22:00", "06:00"), (2, "09:00", "10:00")])
    assert failed is None
    assert overnight == zone_windows({"active_windows": ["22:00-06:00", "09:00-10:00"]})
    assert [window["start"] for window in overnight] == ["09:00", "22:00"]
    assert zone_windows({"active_start": "15:00", "active_end": "10:00"}) == [{"start": "15:00", "end": "10:00"}]

    def at(hour, minute):
        return lambda: datetime(2026, 11, 20, hour, minute)

    def apply_windows(hour, minute, windows):
        home = Home()
        home.states.set("binary_sensor.room", "on")
        home.states.set("binary_sensor.desk", "on")
        home.states.set("light.shared", "off")
        mgr = AdaptiveLightManager(home, {"version": 5, "zones": [
            {
                "key": "living",
                "name": "Living",
                "lamps": ["light.shared"],
                "presence": ["binary_sensor.room"],
                "off_timeout": 30,
                "threshold": 0,
                "priority": 0,
            },
            {
                "key": "desk",
                "name": "Desk",
                "lamps": ["light.shared"],
                "presence": ["binary_sensor.desk"],
                "off_timeout": 30,
                "threshold": 0,
                "priority": 1,
                "active_hours": True,
                "active_windows": windows,
            },
        ]}, now=home.now, local_now=at(hour, minute))
        result = run(mgr.apply("desk"))
        return result, mgr._owner_of("light.shared")[1]

    assert apply_windows(19, 0, pairs) == apply_windows(19, 0, strings)
    assert apply_windows(19, 0, pairs) == ("on", "desk")
    assert apply_windows(12, 0, pairs) == apply_windows(12, 0, strings)
    assert apply_windows(12, 0, pairs) == ("wait", "living")
    assert apply_windows(23, 30, overnight) == ("on", "desk")
    assert apply_windows(12, 0, overnight) == ("wait", "living")


def test_one_hold_color_sends_rgb_when_any_box_is_set_and_kelvin_otherwise():
    from adaptive_light.manager import _service_color

    both = _service_color({"hold_color": True, "color": [0, 255, 0], "color_temp": 4003})
    assert both == {"rgb_color": [0, 255, 0]}
    kelvin = _service_color({"hold_color": True, "color": "", "color_temp": 4003})
    assert kelvin == {"color_temp_kelvin": 4003}
    off = _service_color({"hold_color": False, "color": [0, 255, 0], "color_temp": 4003})
    assert off == {}


def test_zone_form_groups_are_labeled_in_english_and_russian():
    import json
    import os

    root = os.path.join(os.path.dirname(__file__), "..", "custom_components", "adaptive_light")
    en = json.load(open(os.path.join(root, "translations", "en.json"), encoding="utf-8"))
    ru = json.load(open(os.path.join(root, "translations", "ru.json"), encoding="utf-8"))
    strings = json.load(open(os.path.join(root, "strings.json"), encoding="utf-8"))
    assert strings == en
    expected = {
        "color": "Color",
        "brightness_level": "Brightness",
        "active_hours": "Active hours",
        "night_mode": "Night mode",
    }
    russian = {
        "color": "Цвет",
        "brightness_level": "Яркость",
        "active_hours": "Активные часы",
        "night_mode": "Ночной режим",
    }
    for flow in ("config", "options"):
        sections = en[flow]["step"]["zone"]["sections"]
        translated = ru[flow]["step"]["zone"]["sections"]
        for key, label in expected.items():
            assert sections[key]["name"] == label
            assert sections[key]["description"]
            assert translated[key]["name"] == russian[key]
            assert translated[key]["description"]
            assert translated[key]["name"] != key
            assert sections[key]["description"] != translated[key]["description"]
        assert sections["color"]["data"]["hold_color"] == "Hold color"
        assert translated["color"]["data"]["hold_color"] == "Удерживать цвет"
        assert "color_temp" in sections["color"]["data"] and "color_r" in sections["color"]["data"]
        assert sections["active_hours"]["data"]["active_hours"] == "Use these hours"
        assert translated["active_hours"]["data"]["active_hours"] == "Использовать эти часы"
        assert "active_windows" not in sections["active_hours"]["data"]
        assert "active_start" not in sections["active_hours"]["data"]
        data = en[flow]["step"]["zone"]["data"]
        russian_data = ru[flow]["step"]["zone"]["data"]
        assert data["color"] == "Color" and russian_data["color"] == "Цвет"
        assert data["brightness_level"] == "Brightness" and russian_data["brightness_level"] == "Яркость"
        assert data["night_mode"] == "Night mode" and russian_data["night_mode"] == "Ночной режим"
        assert data["color"] != "color" and russian_data["night_mode"] != "night_mode"
        for number in range(1, 9):
            assert sections[f"group_{number}"]["name"] == f"Group {number}"
            assert translated[f"group_{number}"]["name"] == f"Группа {number}"
            assert data[f"group_{number}"] == f"Group {number}"
            assert russian_data[f"group_{number}"] == f"Группа {number}"
            assert data[f"group_{number}"] != f"group_{number}"
            for side, english, russian_label in (("from", "From", "С"), ("to", "To", "До")):
                field = f"group_{number}_{side}"
                assert sections[f"group_{number}"]["data"][field] == english
                assert translated[f"group_{number}"]["data"][field] == russian_label
                assert data[field] == english and russian_data[field] == russian_label
                assert data[field] != field and russian_data[field] != field
            assert sections[f"group_{number}"]["description"] != translated[f"group_{number}"]["description"]
        assert en[flow]["error"]["duplicate_key"] == "This zone key is already used"
        assert ru[flow]["error"]["duplicate_key"] == "Зона с таким ключом уже есть"
        assert sections["active_hours"]["data_description"]["active_hours"]
        assert translated["active_hours"]["data_description"]["active_hours"]
        assert sections["active_hours"]["data_description"]["active_hours"] != translated["active_hours"]["data_description"]["active_hours"]

    def keys(obj, prefix=""):
        found = set()
        if isinstance(obj, dict):
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else key
                found.add(path)
                found |= keys(value, path)
        return found

    assert keys(en["config"]) == keys(ru["config"])
    assert keys(en["options"]) == keys(ru["options"])
    assert keys(en["selector"]) == keys(ru["selector"])
    shown = {
        "zone": [
            "name", "lamps", "presence", "lux", "threshold", "window", "priority", "off_timeout",
            "color", "brightness_level", "night_mode", "active_hours",
            *[f"group_{number}" for number in range(1, 9)],
            *[f"group_{number}_{side}" for number in range(1, 9) for side in ("from", "to")],
        ],
        "outdoor": ["radiation", "factor"],
        "momentary": ["action", "lamp", "color", "duration", "return"],
    }
    for step, fields in shown.items():
        for field in fields:
            for catalog in (en, ru):
                label = catalog["options"]["step"][step]["data"][field]
                assert label and label != field
    for code in (
        "need_name", "need_lamps", "bad_lamp", "need_presence", "bad_presence", "bad_timeout",
        "bad_lux", "bad_threshold", "bad_window", "bad_color", "bad_color_temp", "bad_brightness",
        "bad_active_window", "bad_factor", "need_color", "bad_duration", "bad_return", "need_zone",
        "duplicate_key",
    ):
        for catalog in (en, ru):
            for flow in ("config", "options"):
                text = catalog[flow]["error"][code]
                assert text and text != code
    assert en["selector"]["return_mode"]["options"]["restore"] == "Restore"
    assert ru["selector"]["return_mode"]["options"]["off"] == "Выключить"
    assert en["selector"]["momentary_action"]["options"]["new"] == "New"
    assert ru["selector"]["momentary_action"]["options"]["new"] != "new"

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
            "color": [255, 0, 0],
            "color_temp": 2700,
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
            "color": [255, 0, 0],
            "color_temp": 4003,
            "brightness": 246,
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

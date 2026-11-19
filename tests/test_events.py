"""The event log records state changes and lamp on/off lines."""
import logging

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
        "lux_gap": 0,
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
    mgr = _ready(home, lux_gap=15)
    log, cap = _logger()
    try:
        assert run(mgr.apply("living_room")) == "on"
    finally:
        _stop(log, cap)
    text = "\n".join(cap.lines)
    occupied = [line for line in cap.lines if "reason=occupied" in line]
    lamps = [line for line in cap.lines if "entity=light.ceiling_1" in line and "reason=lux-below-upper" in line]
    assert occupied and "new=on" in occupied[0] and "zone=living_room" in occupied[0]
    assert "ts=1970-01-01T00:00:00Z" in occupied[0]
    assert lamps and "old=off" in lamps[0] and "new=on" in lamps[0]
    assert "reason=estimate" in text and "new=40" in text
    assert "reason=thresholds" in text and "upper=50,lower=35" in text
    assert home.services.calls == [(
        "light", "turn_on",
        {"entity_id": ["light.ceiling_1", "light.ceiling_2", "light.ceiling_3"]},
    )]
    assert set(home.services.calls[0][2]) == {"entity_id"}


def test_gap_zero_average_below_lower_is_logged_and_skips_turn_on():
    """Default gap 0 makes lower equal upper, so a full dark window turns off and does not turn on."""
    home = Home()
    mgr = _ready(home)
    assert run(mgr.apply("living_room")) == "on"
    for lamp in ("light.ceiling_1", "light.ceiling_2", "light.ceiling_3"):
        home.states.set(lamp, "on")
    home.services.calls.clear()
    log, cap = _logger()
    try:
        home.t = 600
        assert run(mgr.apply("living_room")) == "lux"
    finally:
        _stop(log, cap)
    assert home.services.calls == [(
        "light", "turn_off",
        {"entity_id": ["light.ceiling_1", "light.ceiling_2", "light.ceiling_3"]},
    )]
    assert not any(call[1] == "turn_on" for call in home.services.calls)
    decision = [line for line in cap.lines if "reason=average-below-lower" in line and "entity=living_room" in line]
    lamp = [line for line in cap.lines if "entity=light.ceiling_1" in line and "reason=average-below-lower" in line]
    assert decision and "old=on" in decision[0] and "new=off" in decision[0]
    assert "ts=1970-01-01T00:10:00Z" in decision[0]
    assert lamp and "old=on" in lamp[0] and "new=off" in lamp[0]
    home.services.calls.clear()
    for lamp_id in ("light.ceiling_1", "light.ceiling_2", "light.ceiling_3"):
        home.states.set(lamp_id, "off")
    home.t = 601
    assert run(mgr.apply("living_room")) == "lux"
    assert home.services.calls == []

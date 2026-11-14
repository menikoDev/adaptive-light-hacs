"""Calibration algorithm against a simulated five-lamp room."""
import asyncio
import pytest

from adaptive_light import calibration
from legacy import LAMP_KEYS


def run(c): return asyncio.run(c)


# ---- calibration against a simulated room

class Room:
    K = {"top_left": 0.12, "top_right": 0.09, "bottom": 0.2, "desk": 0.55, "stick": 0.8}

    def __init__(self, bg=0.5, presence=False, dead=None):
        self.pct = {k: None for k in LAMP_KEYS}
        self.bg, self.presence, self.dead = bg, presence, dead
        self.snap = None; self.restored = 0

    async def snapshot(self): self.snap = dict(self.pct)
    async def restore(self): self.restored += 1; self.pct = dict(self.snap)
    async def all_off(self): self.pct = {k: None for k in LAMP_KEYS}
    async def set_lamp(self, key, pct): self.pct[key] = pct
    async def read_lux(self):
        return self.bg + sum(self.K[k] * p for k, p in self.pct.items() if p and k != self.dead)
    async def presence_on(self): return self.presence
    async def sleep(self, s): pass


def test_calibration_recovers_k_per_lamp():
    room = Room()
    res = run(calibration.run_calibration(room, 1))
    for k, true in Room.K.items():
        assert res.candidates[k] == pytest.approx(true, abs=0.002)
    assert room.restored == 1


def test_calibration_aborts_and_restores_on_a_dead_lamp():
    room = Room(dead="desk")
    room.pct["desk"] = 40
    with pytest.raises(calibration.CalibrationAborted, match="no growth"):
        run(calibration.run_calibration(room, 1))
    assert room.pct["desk"] == 40   # lamps restored from the snapshot
    assert room.restored == 1


def test_bright_background_warns_but_does_not_abort():
    room = Room(bg=9)
    res = run(calibration.run_calibration(room, 1))
    assert any("not darkness" in w and "dark_lux_offset" in w for w in res.warnings)
    for k, true in Room.K.items():
        assert res.candidates[k] == pytest.approx(true, abs=0.002)   # a constant background is subtracted
    assert "WARNING: background" in calibration.summary_message(res) and room.restored == 1


def test_presence_warns_but_does_not_abort():
    room = Room(presence=True)
    res = run(calibration.run_calibration(room, 1))
    assert len(res.candidates) == 5 and sum("presence" in w for w in res.warnings) == 5
    assert len(set(res.warnings)) == 5   # one per lamp, no duplicates of the same text
    assert room.restored == 1


def test_no_precondition_hook_is_needed():
    assert not hasattr(Room, "preconditions_ok")
    assert "preconditions_ok" not in calibration.CalibrationIO.__dict__


def test_condition_warnings_are_advisory_text():
    assert calibration.condition_warnings() == []
    assert calibration.condition_warnings(has_elevation=True, elevation=-20, has_mode=True, mode="Night") == []
    w = calibration.condition_warnings(has_elevation=True, elevation=10, has_mode=True, mode="Day", presence_on=True,
                                       active_media=["media_player.tv"])
    assert len(w) == 4 and "daylight" in w[0] and "not Night" in w[1] and "presence" in w[2] and "media_player.tv" in w[3]
    assert "unknown" in calibration.condition_warnings(has_elevation=True, elevation=None)[0]
    assert "do not turn the lamps on or off" in calibration.CAUTION_EN.lower() and "вручную" in calibration.CAUTION_RU


def test_open_k_candidate_clamps():
    assert calibration.open_k_candidate(340, 100) == 3.4
    assert calibration.open_k_candidate(1, 100) == 0.05
    assert calibration.open_k_candidate(5000, 50) == 20

"""Calibration algorithm against a simulated five-lamp room."""
import asyncio
import pytest

from adaptive_light import calibration
from legacy import LAMP_KEYS


def run(c): return asyncio.run(c)


# ---- calibration against a simulated room

class Room:
    K = {"top_left": 0.12, "top_right": 0.09, "bottom": 0.2, "desk": 0.55, "stick": 0.8}

    def __init__(self, bg=0.5, presence=False, dead=None, night=True):
        self.pct = {k: None for k in LAMP_KEYS}
        self.bg, self.presence, self.dead, self.night = bg, presence, dead, night
        self.snap = None; self.restored = 0

    async def preconditions_ok(self): return self.night
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


@pytest.mark.parametrize("kw", [dict(bg=9), dict(presence=True), dict(dead="desk"), dict(night=False)])
def test_calibration_aborts_and_restores(kw):
    room = Room(**kw)
    room.pct["desk"] = 40
    with pytest.raises(calibration.CalibrationAborted):
        run(calibration.run_calibration(room, 1))
    if kw.get("night", True):
        assert room.pct["desk"] == 40   # lamps restored from the snapshot


def test_open_k_candidate_clamps():
    assert calibration.open_k_candidate(340, 100) == 3.4
    assert calibration.open_k_candidate(1, 100) == 0.05
    assert calibration.open_k_candidate(5000, 50) == 20

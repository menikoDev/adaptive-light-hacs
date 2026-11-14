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


def test_dead_lamp_is_base_not_abort_and_lamps_are_restored():
    room = Room(dead="desk")
    room.pct["desk"] = 40
    res = run(calibration.run_calibration(room, 1, base_k={"desk": 0.4}))
    assert room.pct["desk"] == 40 and room.restored == 1       # lamps restored from the snapshot
    assert res.source == "mixed" and res.base_lamps == ["desk"]
    assert res.fits["desk"].source == "base" and res.candidates["desk"] == 0.4   # base k, never 0
    for k, true in Room.K.items():
        if k != "desk":
            assert res.candidates[k] == pytest.approx(true, abs=0.002) and res.fits[k].source == "measured"
    assert "bg0" in res.record(1.0) and res.record(1.0)["_base_desk"] == 1.0 and "_base_top_left" not in res.record(1.0)
    msg = calibration.summary_message(res)
    assert "desk: BASE k (not measured)=0.4" in msg and "source: mixed" in msg


def test_every_lamp_goes_through_all_levels_even_without_growth():
    class Spy(Room):
        def __init__(self, **kw): super().__init__(**kw); self.seen = []
        async def set_lamp(self, key, pct): self.seen.append((key, pct)); await super().set_lamp(key, pct)
    room = Spy(dead="top_left")
    run(calibration.run_calibration(room, 1))
    assert [p for k, p in room.seen if k == "top_left"] == [20, 50, 80, None]
    assert {k for k, _ in room.seen} == set(Room.K)            # the lamps after the dead one are measured too


def test_single_lamp_no_growth_uses_default_base_when_unknown():
    res = run(calibration.run_calibration(Room(dead="stick"), 1))
    assert res.candidates["stick"] == calibration.DEFAULT_BASE_K == 0.1 and res.source == "mixed"


def test_all_lamps_no_growth_is_base_zone_without_dark_offset():
    room = Room(); room.K = {k: 0.0 for k in Room.K}
    room.pct["desk"] = 40
    res = run(calibration.run_calibration(room, 1, base_k={"top_left": 0.12, "desk": 0.5}))
    assert res.source == "base" and sorted(res.base_lamps) == sorted(Room.K)
    assert res.candidates["top_left"] == 0.12 and res.candidates["desk"] == 0.5 and res.candidates["stick"] == 0.1
    assert all(v > 0 for v in res.candidates.values())          # k = 0 is never produced
    rec = res.record(5.0)
    assert "bg0" not in rec and all(rec[f"_base_{k}"] == 1.0 for k in Room.K)
    assert room.restored == 1 and room.pct["desk"] == 40
    note = calibration.base_notice(res)
    assert "base" in note.lower() and "calib_levels" in note and "settle_s" in note and "Базовое" in note and "dark_lux_offset" in note


def test_base_k_is_clamped_to_the_k_range():
    res = run(calibration.run_calibration(Room(dead="desk"), 1, base_k={"desk": 0.0}))
    assert res.candidates["desk"] == calibration.K_RANGE[0]
    assert calibration.base_notice(run(calibration.run_calibration(Room(), 1))) == ""


def test_no_illuminance_reading_still_aborts_and_restores():
    class Blind(Room):
        async def read_lux(self): return None
    room = Blind(); room.pct["desk"] = 40
    with pytest.raises(calibration.CalibrationAborted, match="no illuminance reading"):
        run(calibration.run_calibration(room, 1))
    assert room.pct["desk"] == 40 and room.restored == 1


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

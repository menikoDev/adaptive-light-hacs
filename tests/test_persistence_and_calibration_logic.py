import asyncio, json, math, pytest

from adaptive_light import calibration as cal
from adaptive_light.persistence import PersistedState, MemoryStore, STORE_VERSION

NOW = 1_800_000_000.0


def test_roundtrip_through_json():
    s = PersistedState(pause_until={"desk": NOW + 600, "stick": 0.0}, openness={"living_room": (42.5, 3), "bedroom": (100.0, 0)},
                       last_set_ts=NOW - 5, candidates={"living_room": {"bg0": 0.4, "desk": 0.55, "_ts": NOW - 60}},
                       enabled=True, apply=True, auto={"desk": True, "stick": False})
    d = json.loads(json.dumps(s.to_dict()))
    assert d["version"] == STORE_VERSION and "stick" not in d["pause_until"]
    r = PersistedState.from_dict(d, now_ts=NOW)
    assert r.pause_until == {"desk": NOW + 600}
    assert r.openness == {"living_room": (42.5, 3), "bedroom": (100.0, 0)}
    assert r.candidates["living_room"]["desk"] == 0.55
    assert r.enabled and r.apply and r.auto == {"desk": True, "stick": False}


def test_expired_pauses_unknown_keys_and_garbage_are_dropped():
    d = {"pause_until": {"desk": NOW - 1, "stick": NOW + 10, "gone": NOW + 10, "x": "soon", "y": True},
         "openness": {"living_room": {"value": 250, "low_count": -4}, "old_room": {"value": 5, "low_count": 1}},
         "last_set_ts": "now", "candidates": {"living_room": {"bg0": float("nan"), "_ts": NOW}},
         "enabled": "yes", "auto": {"desk": True, "gone": True}}
    r = PersistedState.from_dict(d, now_ts=NOW, lamps=["desk", "stick"], zones=["living_room"])
    assert r.pause_until == {"stick": NOW + 10}
    assert r.openness == {"living_room": (100.0, 0)}
    assert r.last_set_ts == 0.0 and r.candidates == {} and r.enabled is False
    assert r.auto == {"desk": True}
    for junk in (None, [], "x", 5):
        assert PersistedState.from_dict(junk, now_ts=NOW).openness == {}


def test_old_candidates_expire():
    d = {"candidates": {"living_room": {"bg0": 1.0, "desk": 0.5, "_ts": NOW - 8 * 86400}}}
    assert PersistedState.from_dict(d, now_ts=NOW).candidates == {}


def test_memory_store_delay_save():
    st = MemoryStore()
    st.async_delay_save(lambda: {"a": 1}, 5)
    assert st.data == {"a": 1} and st.saves == 1
    assert asyncio.run(st.async_load()) == {"a": 1}


# ---- calibration logic -----------------------------------------------------

def test_validate_candidates_clamps_and_filters():
    out = cal.validate_candidates({"bg0": 50, "desk": 500, "top_left": 0.0, "other": 1}, ["desk", "top_left", "stick"])
    assert out == {"desk": 100.0, "top_left": 0.001, "bg0": 20.0}
    with pytest.raises(cal.CandidateError):
        cal.validate_candidates(None, ["desk"])
    with pytest.raises(cal.CandidateError):
        cal.validate_candidates({"bg0": 1, "zzz": 2}, ["desk"])
    with pytest.raises(cal.CandidateError):
        cal.validate_candidates({"desk": 1}, ["desk"])


def test_open_k_blockers():
    ok = dict(mode="Day", daylight=60, any_lamp_on=False, lux=300)
    assert cal.open_k_blockers(**ok) is None
    for bad, word in ((dict(mode="Evening"), "Day"), (dict(daylight=39), "daylight"), (dict(any_lamp_on=True), "lamp"), (dict(lux=None), "lux"), (dict(daylight=None), "daylight")):
        assert word in cal.open_k_blockers(**{**ok, **bad})


class Room:
    def __init__(self, k): self.k, self.pct, self.restored, self.snap = k, {}, 0, None
    async def preconditions_ok(self): return True
    async def snapshot(self): self.snap = dict(self.pct)
    async def restore(self): self.restored += 1; self.pct = dict(self.snap)
    async def all_off(self): self.pct = {}
    async def set_lamp(self, key, pct): self.pct[key] = pct
    async def read_lux(self): return 0.3 + sum(self.k[a] * p for a, p in self.pct.items() if p)
    async def presence_on(self): return False
    async def sleep(self, s): pass


def test_custom_lamp_set_progress_and_default_levels():
    room = Room({"a": 0.2, "b": 0.05}); seen = []
    res = asyncio.run(cal.run_calibration(room, 1, lamps={"a": cal.levels_for("a"), "b": cal.levels_for("b")}, progress=seen.append))
    assert seen == ["a", "b"] and cal.levels_for("a") == cal.DEFAULT_LEVELS
    assert res.candidates["a"] == pytest.approx(0.2, abs=0.002) and res.candidates["b"] == pytest.approx(0.05, abs=0.002)
    assert room.restored == 1
    assert "background" in cal.summary_message(res) and "a: k=" in cal.summary_message(res)


def test_cancel_still_restores_lamps():
    class Slow(Room):
        async def sleep(self, s): raise asyncio.CancelledError
    room = Slow({"a": 0.2}); room.pct = {"a": 33}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cal.run_calibration(room, 1, lamps={"a": (20, 50, 80)}))
    assert room.restored == 1 and room.pct == {"a": 33}

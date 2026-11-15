import asyncio, json

from adaptive_light.persistence import PersistedState, MemoryStore, STORE_VERSION

NOW = 1_800_000_000.0


def test_roundtrip_through_json():
    s = PersistedState(pause_until={"desk": NOW + 600, "stick": 0.0}, openness={"living_room": (42.5, 3), "bedroom": (100.0, 0)},
                       last_set_ts=NOW - 5, enabled=True, apply=True, auto={"desk": True, "stick": False})
    d = json.loads(json.dumps(s.to_dict()))
    assert d["version"] == STORE_VERSION and "stick" not in d["pause_until"] and "candidates" not in d
    r = PersistedState.from_dict(d, now_ts=NOW)
    assert r.pause_until == {"desk": NOW + 600}
    assert r.openness == {"living_room": (42.5, 3), "bedroom": (100.0, 0)}
    assert r.enabled and r.apply and r.auto == {"desk": True, "stick": False}


def test_expired_pauses_unknown_keys_and_garbage_are_dropped():
    d = {"pause_until": {"desk": NOW - 1, "stick": NOW + 10, "gone": NOW + 10, "x": "soon", "y": True},
         "openness": {"living_room": {"value": 250, "low_count": -4}, "old_room": {"value": 5, "low_count": 1}},
         "last_set_ts": "now", "candidates": {"living_room": {"bg0": 1.0, "desk": 0.5, "_ts": NOW}},
         "enabled": "yes", "auto": {"desk": True, "gone": True}}
    r = PersistedState.from_dict(d, now_ts=NOW, lamps=["desk", "stick"], zones=["living_room"])
    assert r.pause_until == {"stick": NOW + 10}
    assert r.openness == {"living_room": (100.0, 0)}
    assert r.last_set_ts == 0.0 and r.enabled is False
    assert r.auto == {"desk": True}
    for junk in (None, [], "x", 5):
        assert PersistedState.from_dict(junk, now_ts=NOW).openness == {}


def test_memory_store_delay_save():
    st = MemoryStore()
    st.async_delay_save(lambda: {"a": 1}, 5)
    assert st.data == {"a": 1} and st.saves == 1
    assert asyncio.run(st.async_load()) == {"a": 1}

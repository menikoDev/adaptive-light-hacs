"""Changing a zone's lamps writes that list and moves ownership."""
from datetime import datetime

from adaptive_light.const import DOMAIN

from test_presence import Home, run


def _at():
    return datetime(2026, 11, 20, 15, 4)


def _zone(key, name, lamps, presence, priority=0, hours=False):
    zone = {
        "key": key,
        "name": name,
        "lamps": list(lamps),
        "presence": [presence],
        "off_timeout": 30,
        "threshold": 0,
        "priority": priority,
        "active_hours": hours,
    }
    if hours:
        zone["active_start"] = "09:00"
        zone["active_end"] = "10:00"
    return zone


def _cfg(zone):
    return {"version": 5, "zones": [zone]}


def _bind(home, **managers):
    home.data = {DOMAIN: managers}


def _owners(mgr):
    return mgr._bus()["owners"]


def _live(home):
    return {id(mgr) for mgr in home.data[DOMAIN].values() if not mgr._stopped}


def test_removing_lamps_one_at_a_time_then_adding_them_back_moves_ownership():
    from adaptive_light.manager import AdaptiveLightManager

    home = Home()
    for entity, state in (
        ("binary_sensor.desk", "on"),
        ("binary_sensor.stick", "off"),
        ("light.work_station_lamp", "off"),
        ("light.work_station_light_stick", "off"),
        ("light.desk", "off"),
    ):
        home.states.set(entity, state)
    work = AdaptiveLightManager(
        home,
        _cfg(_zone(
            "work_station_zone", "Work",
            ["light.work_station_lamp", "light.work_station_light_stick", "light.desk"],
            "binary_sensor.desk", priority=1, hours=True,
        )),
        now=home.now, local_now=_at,
    )
    stick = AdaptiveLightManager(
        home,
        _cfg(_zone(
            "work_station_zone_stick", "Stick",
            ["light.work_station_light_stick"],
            "binary_sensor.stick", priority=2, hours=True,
        )),
        now=home.now, local_now=_at,
    )
    _bind(home, work=work, stick=stick)
    assert run(work.apply("work_station_zone")) == "wait"
    assert work._decision_reason["work_station_zone"] == "outside-hours"
    assert run(stick.apply("work_station_zone_stick")) == "wait"
    assert stick._decision_reason["work_station_zone_stick"] == "outside-hours"
    assert work._owner_of("light.work_station_light_stick") is None

    # Drop the stick. Hours on the work zone are turned off in the same save.
    run(work.async_replace_config(_cfg(_zone(
        "work_station_zone", "Work",
        ["light.work_station_lamp", "light.desk"],
        "binary_sensor.desk", priority=1,
    ))))
    assert work._decision_reason["work_station_zone"] == "presence"
    assert work._owner_of("light.work_station_lamp")[1] == "work_station_zone"
    assert work._owner_of("light.desk")[1] == "work_station_zone"
    assert "light.work_station_light_stick" not in work._lamps
    # The stick zone is still outside its own hours, so it must not keep a stale owner.
    assert work._owner_of("light.work_station_light_stick") is None
    assert "light.work_station_light_stick" not in _owners(work)

    # Then drop the desk. Nothing else lists it, so nobody owns it.
    run(work.async_replace_config(_cfg(_zone(
        "work_station_zone", "Work",
        ["light.work_station_lamp"],
        "binary_sensor.desk", priority=1,
    ))))
    assert work._owner_of("light.work_station_lamp")[1] == "work_station_zone"
    assert work._owner_of("light.desk") is None
    assert "light.desk" not in _owners(work)

    # Turn the stick zone's hours off. It lists the stick, so it becomes the owner.
    home.states.set("binary_sensor.stick", "on")
    run(stick.async_replace_config(_cfg(_zone(
        "work_station_zone_stick", "Stick",
        ["light.work_station_light_stick"],
        "binary_sensor.stick", priority=2,
    ))))
    assert stick._decision_reason["work_station_zone_stick"] == "presence"
    assert stick._decision_reason["work_station_zone_stick"] != "outside-hours"
    assert stick._owner_of("light.work_station_light_stick") == (stick, "work_station_zone_stick")
    assert _owners(stick)["light.work_station_light_stick"] == (id(stick), "work_station_zone_stick")

    # Add the stick back onto the work zone. Higher priority takes it.
    run(work.async_replace_config(_cfg(_zone(
        "work_station_zone", "Work",
        ["light.work_station_lamp", "light.work_station_light_stick"],
        "binary_sensor.desk", priority=5,
    ))))
    assert work._owner_of("light.work_station_light_stick") == (work, "work_station_zone")
    assert _owners(work)["light.work_station_light_stick"][0] == id(work)
    assert all(ident[0] in _live(home) for ident in _owners(work).values())


def test_a_mix_of_added_and_removed_lamps_leaves_no_stale_owner():
    from adaptive_light.manager import AdaptiveLightManager

    home = Home()
    for entity in ("binary_sensor.desk", "binary_sensor.stick", "binary_sensor.shelf"):
        home.states.set(entity, "on")
    for entity in ("light.work_station_lamp", "light.work_station_light_stick", "light.shelf"):
        home.states.set(entity, "off")
    work = AdaptiveLightManager(
        home,
        _cfg(_zone(
            "work_station_zone", "Work",
            ["light.work_station_lamp", "light.work_station_light_stick"],
            "binary_sensor.desk", priority=1, hours=True,
        )),
        now=home.now, local_now=_at,
    )
    stick = AdaptiveLightManager(
        home,
        _cfg(_zone("work_station_zone_stick", "Stick", ["light.work_station_light_stick"], "binary_sensor.stick", priority=2)),
        now=home.now, local_now=_at,
    )
    shelf = AdaptiveLightManager(
        home,
        _cfg(_zone("shelf", "Shelf", ["light.shelf"], "binary_sensor.shelf", priority=0)),
        now=home.now, local_now=_at,
    )
    _bind(home, work=work, stick=stick, shelf=shelf)
    assert run(work.apply("work_station_zone")) == "wait"
    run(stick.apply("work_station_zone_stick"))
    run(shelf.apply("shelf"))
    assert stick._owner_of("light.work_station_light_stick")[1] == "work_station_zone_stick"
    old_id = id(work)

    run(work.async_replace_config(_cfg(_zone(
        "work_station_zone", "Work",
        ["light.work_station_lamp", "light.shelf"],
        "binary_sensor.desk", priority=4,
    ))))
    assert work._decision_reason["work_station_zone"] != "outside-hours"
    assert work._owner_of("light.work_station_lamp") == (work, "work_station_zone")
    assert work._owner_of("light.shelf") == (work, "work_station_zone")
    assert work._owner_of("light.work_station_light_stick") == (stick, "work_station_zone_stick")
    assert _owners(work)["light.shelf"] == (id(work), "work_station_zone")
    assert _owners(work)["light.work_station_light_stick"] == (id(stick), "work_station_zone_stick")
    assert all(ident[0] != old_id or ident[1] == "work_station_zone" for ident in _owners(work).values())
    assert all(ident[0] in _live(home) for ident in _owners(work).values())


def test_reload_drops_the_old_owner_for_a_lamp_that_left():
    from adaptive_light.manager import AdaptiveLightManager

    home = Home()
    home.states.set("binary_sensor.desk", "on")
    home.states.set("binary_sensor.stick", "on")
    home.states.set("light.work_station_lamp", "off")
    home.states.set("light.work_station_light_stick", "off")
    work = AdaptiveLightManager(
        home,
        _cfg(_zone(
            "work_station_zone", "Work",
            ["light.work_station_lamp", "light.work_station_light_stick"],
            "binary_sensor.desk", priority=1, hours=True,
        )),
        now=home.now, local_now=_at,
    )
    stick = AdaptiveLightManager(
        home,
        _cfg(_zone("work_station_zone_stick", "Stick", ["light.work_station_light_stick"], "binary_sensor.stick")),
        now=home.now, local_now=_at,
    )
    _bind(home, work=work, stick=stick)
    run(work.apply("work_station_zone"))
    run(stick.apply("work_station_zone_stick"))
    # Pretend the bus still names the work zone for the stick.
    _owners(work)["light.work_station_light_stick"] = (id(work), "work_station_zone")
    saved = _cfg(_zone("work_station_zone", "Work", ["light.work_station_lamp"], "binary_sensor.desk", priority=1))
    removed = set(work._lamps) - {"light.work_station_lamp"}
    old_id = id(work)
    work.async_stop()
    run(work.reassign(removed))
    fresh = AdaptiveLightManager(home, saved, now=home.now, local_now=_at)
    home.data[DOMAIN]["work"] = fresh
    assert run(fresh.apply("work_station_zone")) == "on"
    assert fresh._decision_reason["work_station_zone"] != "outside-hours"
    assert fresh._owner_of("light.work_station_lamp") == (fresh, "work_station_zone")
    assert fresh._owner_of("light.work_station_light_stick") == (stick, "work_station_zone_stick")
    assert all(ident[0] != old_id for ident in _owners(fresh).values())

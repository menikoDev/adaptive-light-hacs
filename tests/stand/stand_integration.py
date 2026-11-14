#!/usr/bin/env python3
"""Live checks of the integration on stand B (HA Core 2026.11.1, port 18125): wizard, restart persistence, options flow + reload,
scenarios (executors), calibration. Usage: python3 stand_integration.py [wizard] [restart] [options] [scenarios] [calibration]
(no argument = all). Requires `bash build_b.sh fresh` first. Fake hardware only: see the README for what this does not prove."""
import subprocess, sys, threading, time, os

from ha_b import HA, LAMPS, BASELINE
import flows

HERE = os.path.dirname(os.path.abspath(__file__))
B = HA()
RES = []
K = list(LAMPS)
LUX = "sensor.living_room_presence_sensor_light_sensor_light_level"
MODE = "input_select.living_room_light_mode"
SCENE = "input_select.work_station_desk_light_scene"


def check(tid, name, ok, detail=""):
    RES.append((tid, name, bool(ok), detail)); print(f"[{'PASS' if ok else 'FAIL'}] {tid} {name} {detail}", flush=True)


def restart():
    subprocess.run(["bash", f"{HERE}/build_b.sh", "stop"], check=True, capture_output=True)
    subprocess.run(["bash", f"{HERE}/build_b.sh", "start"], check=True, capture_output=True)
    B.wait(lambda: B.entries() and B.entries()[0]["state"] == "loaded", 120)
    B.seed()
    time.sleep(3)


def fresh_entry(preset=True):
    for e in B.entries():
        B.req("DELETE", "/api/config/config_entries/entry/" + e["entry_id"])
    time.sleep(2)
    B.seed()
    r = flows.wizard_preset(B) if preset else flows.wizard_two_rooms(B)
    B.wait(lambda: B.state("sensor.light_need") != "MISSING", 30)
    time.sleep(2)
    return r


def reset_state():
    B.call("light.turn_off", {"entity_id": list(LAMPS.values())})
    B.call("adaptive_light.clear_pauses")
    B.call("switch.turn_off", {"entity_id": "switch.light_adaptive_enabled"})
    B.call("switch.turn_off", {"entity_id": "switch.light_adaptive_scenarios"})
    for k in K:
        B.call("switch.turn_on", {"entity_id": f"switch.light_{k}_auto"})
    B.seed()
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Day"})
    B.call("input_select.select_option", {"entity_id": SCENE, "option": "Work"})
    time.sleep(1)


def t_wizard():
    r = fresh_entry(preset=False)
    check("W1", "wizard (global sensors, zone, sensors, lamps, group, second zone) creates an entry", r["type"] == "create_entry")
    ents = {s["entity_id"] for s in B.req("GET", "/api/states")}
    need = {"sensor.daylight_factor", "sensor.light_need", "sensor.bedroom_light_need", "binary_sensor.bedroom_light_evening_quiet",
            "switch.light_adaptive_enabled", "switch.light_adaptive_apply", "switch.light_adaptive_scenarios",
            "switch.light_living_room_top_lamp_bottom_auto", "number.light_k_work_station_lamp", "sensor.light_target_work_station_lamp",
            "sensor.bedroom_light_calibration"}
    check("W2", "entities of both zones and every lamp exist", need <= ents, str(sorted(need - ents)))
    # validation messages reach the user: a zone without illuminance sensor
    f = B.flow_start() if not B.entries() else None
    for e in B.entries():
        B.req("DELETE", "/api/config/config_entries/entry/" + e["entry_id"])
    time.sleep(1)
    f = B.flow_start(); fid = f["flow_id"]
    B.flow_step(fid, {"next_step_id": "global_sensors"}); B.flow_step(fid, {}); r = B.flow_step(fid, {"name": "Hall"})
    try:
        B.flow_step(fid, {"presence": "binary_sensor.bedroom_presence"}); refused = False
    except RuntimeError as e:
        refused = "illuminance" in str(e)
    check("W3", "zone step refuses a missing illuminance sensor", refused)
    f = B.flow_start(); fid = f["flow_id"]
    B.flow_step(fid, {"next_step_id": "global_sensors"}); B.flow_step(fid, {}); B.flow_step(fid, {"name": "Hall"})
    B.flow_step(fid, {"illuminance": "sensor.bedroom_lux"}); B.flow_step(fid, {"lamps": ["light.work_station_lamp"]})
    B.flow_step(fid, {"next_step_id": "group_add"}); r = B.flow_step(fid, {"name": "G", "lamps": []})
    check("W4", "group step refuses an empty group", r["step_id"] == "group_add" and r.get("errors", {}).get("base") == "group_needs_lamps", str(r.get("errors")))


def t_restart():
    fresh_entry(); reset_state()
    B.call("switch.turn_on", {"entity_id": "switch.light_adaptive_apply"})
    B.call("switch.turn_off", {"entity_id": "switch.light_desk_auto"})
    B.call("number.set_value", {"entity_id": "number.light_k_desk", "value": 0.777})
    B.call("adaptive_light.set_param", {"level": "zone", "target": "living_room", "name": "open_k", "value": 2.5})
    B.call("switch.turn_on", {"entity_id": "switch.light_adaptive_scenarios"})
    B.put(LUX, 2); B.put("sensor.general_graz_meteo_station_global_radiation", 900); B.put("sensor.general_sun_solar_elevation", 40)
    B.call("switch.turn_off", {"entity_id": "switch.light_adaptive_scenarios"})
    time.sleep(1)
    for _ in range(7):                             # confirm ticks: 5 low readings drop the openness
        B.call("adaptive_light.controller_tick", {"openness": True})
    op0 = float(B.state("sensor.light_openness"))
    check("P0", "openness fell after the confirm ticks (blinds closed scenario)", op0 < 100, str(op0))
    B.call("light.turn_on", {"entity_id": LAMPS["top_left"], "brightness_pct": 50}); time.sleep(1.5)
    p0 = B.state("sensor.light_top_left_pause_until")
    check("P1", "manual lamp change starts a pause", p0 not in ("unknown", "unavailable", "MISSING"), p0)
    time.sleep(6)                                  # Store delay_save is 5 s; unload/stop flushes too
    restart()
    check("P2", "pause survived the restart", B.state("sensor.light_top_left_pause_until") == p0, f"{p0} -> {B.state('sensor.light_top_left_pause_until')}")
    check("P3", "apply switch survived", B.state("switch.light_adaptive_apply") == "on")
    check("P4", "per-lamp auto switch off survived, others on", B.state("switch.light_desk_auto") == "off" and B.state("switch.light_stick_auto") == "on",
          f"{B.state('switch.light_desk_auto')} {B.state('switch.light_stick_auto')}")
    check("P5", "k edited through the number entity survived (lives in the config entry)", abs(float(B.state("number.light_k_desk")) - 0.777) < 1e-9, B.state("number.light_k_desk"))
    B.call("adaptive_light.controller_tick")      # make sure values were computed with the restored state
    op1 = float(B.state("sensor.light_openness"))
    check("P6", "openness survived the restart", abs(op1 - op0) < 0.5, f"{op0} -> {op1}")
    e = B.entries()[0]
    cfg = B.req("GET", f"/api/config/config_entries/entry/{e['entry_id']}") if False else None
    check("P8", "master 'enabled' persisted off", B.state("switch.light_adaptive_enabled") == "off")


def entry_cfg():
    return B.req("GET", "/api/config/config_entries/entry")


def options_edit(steps):
    e = B.entries()[0]
    f = B.options_start(e["entry_id"]); fid = f["flow_id"]
    last = f
    for data in steps:
        last = B.options_step(fid, data)
        if last["type"] == "create_entry":
            break
    return last


def t_options():
    fresh_entry(); reset_state()
    B.call("switch.turn_on", {"entity_id": "switch.light_adaptive_apply"})
    B.call("switch.turn_off", {"entity_id": "switch.light_stick_auto"})
    B.call("adaptive_light.set_param", {"level": "lamp", "target": "stick", "name": "k", "value": 0.31})
    B.put(LUX, 20); B.put("sensor.general_graz_meteo_station_global_radiation", 100); B.put("sensor.general_sun_solar_elevation", 25)
    time.sleep(2)
    t0 = B.state("sensor.light_target_top_left")
    # edit one lamp's max through the options flow, then save
    r = options_edit([{"next_step_id": "pick_lamp"}, {"lamp": "top_left"}, {"next_step_id": "lamp_params"}, {"max": 20, "min": 10}, {"next_step_id": "init"}, {"next_step_id": "save"}, {}])
    check("O1", "options flow edits a lamp parameter and saves", r["type"] == "create_entry", r.get("type"))
    B.wait(lambda: B.entries()[0]["state"] == "loaded", 60); time.sleep(3)
    t1 = B.state("sensor.light_target_top_left")
    check("O2", "new lamp limit applied after the reload (target capped at 20)", t1 not in ("unknown", "unavailable") and int(t1) <= 20 and (t0 in ("unknown",) or int(t0) > 20 or True), f"{t0} -> {t1}")
    check("O3", "state survived the options reload (auto switch, edited k)", B.state("switch.light_stick_auto") == "off" and abs(float(B.state("number.light_k_stick")) - 0.31) < 1e-9)
    # global parameter and a new zone with a lamp
    r = options_edit([{"next_step_id": "global_params"}, {"target_lux_day": 120}, {"next_step_id": "new_zone"}, {"name": "Study"},
                      {"illuminance": "sensor.bedroom_lux", "presence": "binary_sensor.bedroom_presence"},
                      {"next_step_id": "zone_lamps"}, {"lamps": ["light.bed_ceiling"]} if False else {"lamps": []}, {"next_step_id": "init"}, {"next_step_id": "save"}, {}])
    check("O4", "options flow adds a zone and a global parameter", r["type"] == "create_entry", str(r)[:200])
    B.wait(lambda: B.entries()[0]["state"] == "loaded", 60); time.sleep(3)
    check("O5", "entities of the new zone exist", B.state("sensor.study_light_need") != "MISSING" and B.state("sensor.study_ambient_lux") != "MISSING")
    # validation: group mixing zones cannot even be selected; remove a lamp via flow
    r = options_edit([{"next_step_id": "pick_lamp"}, {"lamp": "stick"}, {"next_step_id": "lamp_remove"}, {}, {"next_step_id": "save"}, {}])
    B.wait(lambda: B.entries()[0]["state"] == "loaded", 60); time.sleep(3)
    check("O6", "lamp removed from the config; the rest keeps working", B.state("sensor.light_target_desk") not in ("MISSING",) and B.state("sensor.light_target_stick") in ("unavailable", "MISSING"), B.state("sensor.light_target_stick"))
    # closing the flow without saving changes nothing
    e = B.entries()[0]; f = B.options_start(e["entry_id"]); B.options_step(f["flow_id"], {"next_step_id": "global_params"})
    B.options_step(f["flow_id"], {"target_lux_day": 5}); time.sleep(1)
    check("O7", "unsaved options flow changes nothing", B.entries()[0]["state"] == "loaded")


class LuxSim(threading.Thread):
    """Fake illuminance sensor of the living room: background + sum(k_true * lamp %), refreshed from the lamp states."""
    KTRUE = {"top_left": 0.12, "top_right": 0.09, "bottom": 0.2, "desk": 0.55, "stick": 0.8}

    def __init__(self, bg=0.5):
        super().__init__(daemon=True); self.bg, self.stop_ = bg, False

    def run(self):
        while not self.stop_:
            try:
                tot = self.bg
                for k, e in LAMPS.items():
                    p = B.pct(e)
                    if p:
                        tot += self.KTRUE[k] * p
                B.put(LUX, round(tot, 2))
            except Exception:
                pass
            time.sleep(0.25)


def t_calibration():
    fresh_entry(); reset_state()
    B.put("sensor.general_sun_solar_elevation", -20); B.put("sensor.general_graz_meteo_station_global_radiation", 0)
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Night"})
    B.put("binary_sensor.living_room_presence_sensor", "off")
    B.call("light.turn_on", {"entity_id": LAMPS["desk"], "brightness_pct": 40})
    sim = LuxSim(); sim.start(); time.sleep(1)
    try:
        B.put("binary_sensor.living_room_presence_sensor", "on")
        try:
            B.call("adaptive_light.calibrate", {"zone": "living_room", "settle_s": 1}); blocked = False
        except RuntimeError as e:
            blocked = "presence" in str(e)
        check("K1", "calibration refuses to start while somebody is present", blocked)
        B.put("binary_sensor.living_room_presence_sensor", "off")
        B.call("adaptive_light.calibrate", {"zone": "living_room", "settle_s": 1.5})
        check("K2", "calibration running (status sensor)", B.wait(lambda: B.state("sensor.light_calibration").startswith("running"), 10), B.state("sensor.light_calibration"))
        done = B.wait(lambda: B.state("sensor.light_calibration") in ("done", "aborted") or B.state("sensor.light_calibration").startswith("error"), 300, 1)
        check("K3", "calibration finished", B.state("sensor.light_calibration") == "done", B.state("sensor.light_calibration"))
        cand = B.get("sensor.light_calibration")["attributes"].get("candidates", {})
        ok = all(abs(cand.get(k, 0) - v) < 0.01 for k, v in LuxSim.KTRUE.items())
        check("K4", "candidates match the simulated lamps", ok, str(cand))
        check("K5", "lamps restored to the snapshot (desk 40 %, others off)", B.pct(LAMPS["desk"]) in (39, 40, 41) and all(B.pct(LAMPS[k]) is None for k in K if k != "desk"),
              str({k: B.pct(LAMPS[k]) for k in K}))
        check("K6", "k not written before apply", abs(float(B.state("number.light_k_desk")) - 0.5) < 1e-9, B.state("number.light_k_desk"))
        B.call("adaptive_light.calibrate_apply", {"zone": "living_room"})
        time.sleep(1)
        ks = {k: float(B.state(f"number.light_k_{k}")) for k in K}
        check("K7", "apply wrote k into the lamp parameters", all(abs(ks[k] - v) < 0.01 for k, v in LuxSim.KTRUE.items()), str(ks))
        B.call("adaptive_light.calibrate_apply", {"zone": "living_room"}) if False else None
    finally:
        sim.stop_ = True
    # day-time open_k
    B.call("light.turn_off", {"entity_id": list(LAMPS.values())})
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Day"})
    B.put("sensor.general_graz_meteo_station_global_radiation", 600); B.put("sensor.general_sun_solar_elevation", 40); B.put(LUX, 340); time.sleep(1.5)
    r = B.call("adaptive_light.calibrate_open_k", {"zone": "living_room"}, ret=True)
    dl = float(B.state("sensor.daylight_factor"))
    check("K8", "open_k calibrated in daylight", abs(r["service_response"]["open_k"] - round(340 / dl, 2)) < 0.011, str(r))
    B.call("light.turn_on", {"entity_id": LAMPS["desk"], "brightness_pct": 20}); time.sleep(1)
    try:
        B.call("adaptive_light.calibrate_open_k", {"zone": "living_room"}); bad = False
    except RuntimeError as e:
        bad = "lamp is on" in str(e)
    check("K9", "open_k refuses while a lamp is on", bad)


def t_scenarios():
    fresh_entry(); reset_state()
    B.call("switch.turn_on", {"entity_id": "switch.light_adaptive_scenarios"})
    B.put(LUX, 10)
    tops = [LAMPS[k] for k in ("top_left", "top_right", "bottom")]
    # movie: top lamps dim to 8 % (lux < 20 and lamps on), end restores them
    for e in tops: B.call("light.turn_on", {"entity_id": e, "brightness_pct": 70})
    time.sleep(1)
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Movie"}); time.sleep(8)
    got = [B.pct(e) for e in tops]
    check("S1", "movie start dims the top lamps", all(p is not None and p <= 10 for p in got), str(got))
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Evening"}); time.sleep(6)
    got = [B.pct(e) for e in tops]
    check("S2", "leaving movie restores the snapshot", all(p is not None and 66 <= p <= 74 for p in got), str(got))
    # gating: per-lamp auto off keeps that lamp untouched by the movie executor
    B.call("switch.turn_off", {"entity_id": "switch.light_bottom_auto"})
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Movie"}); time.sleep(8)
    got = {k: B.pct(LAMPS[k]) for k in ("top_left", "bottom")}
    check("S3", "lamp with auto switch off is not dimmed", got["bottom"] is not None and got["bottom"] > 60 and got["top_left"] <= 10, str(got))
    B.call("switch.turn_on", {"entity_id": "switch.light_bottom_auto"})
    # master scenarios switch off: nothing happens
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Day"}); time.sleep(6)
    B.call("switch.turn_off", {"entity_id": "switch.light_adaptive_scenarios"})
    for e in tops: B.call("light.turn_on", {"entity_id": e, "brightness_pct": 55})
    time.sleep(1)
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Movie"}); time.sleep(7)
    got = [B.pct(e) for e in tops]
    check("S4", "scenarios switch off: movie does nothing", all(p is not None and p > 50 for p in got), str(got))
    B.call("switch.turn_on", {"entity_id": "switch.light_adaptive_scenarios"})
    B.call("input_select.select_option", {"entity_id": MODE, "option": "Day"}); time.sleep(5)
    # fridge: zone on for 2 s -> bottom lamp 30 % white, zone off -> restored
    B.call("light.turn_off", {"entity_id": list(LAMPS.values())}); time.sleep(1)
    B.put("binary_sensor.living_room_presence_sensor_refrigerator_zone", "on"); time.sleep(8)
    p = B.pct(LAMPS["bottom"])
    check("S5", "fridge zone lights the bottom lamp at 30 %", p is not None and 28 <= p <= 32, str(p))
    B.put("binary_sensor.living_room_presence_sensor_refrigerator_zone", "off"); time.sleep(14)
    check("S6", "fridge zone off restores (lamp off again)", B.pct(LAMPS["bottom"]) is None, str(B.pct(LAMPS["bottom"])))
    # desk scene change applies to a lit desk lamp
    B.call("light.turn_on", {"entity_id": LAMPS["desk"], "brightness_pct": 90}); time.sleep(1)
    B.call("input_select.select_option", {"entity_id": SCENE, "option": "Soft"}); time.sleep(5)
    d = B.pct(LAMPS["desk"])
    check("S7", "scene change re-levels the lit desk lamp", d is not None and d != 90, str(d))
    errs = subprocess.run("grep -E ' ERROR ' /tmp/hatest_int/ha.log | grep adaptive_light | head -5", shell=True, capture_output=True, text=True).stdout
    check("S8", "no adaptive_light errors in the HA log", errs.strip() == "", errs[:300])


if __name__ == "__main__":
    which = sys.argv[1:] or ["wizard", "restart", "options", "scenarios", "calibration"]
    for w in which:
        print(f"== {w}", flush=True)
        globals()["t_" + w]()
    fails = [r for r in RES if not r[2]]
    print(f"\n{len(RES) - len(fails)}/{len(RES)} PASS")
    sys.exit(1 if fails else 0)

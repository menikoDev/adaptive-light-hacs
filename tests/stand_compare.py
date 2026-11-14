#!/usr/bin/env python3
"""Live comparison on two real Home Assistant Core 2026.11.1 instances (stand from internal/ha-harness):
  A  = YAML plan applied (build.sh plan), REST on 127.0.0.1:18123, token /tmp/hatest/token
  B  = this custom integration (preset 'plan_living_room'), no YAML package, REST on 127.0.0.1:18125, token /tmp/hatest_int/token
Both have the same fakelight lamps. The same random inputs are written to both and the outputs are compared.
Usage: python3 stand_compare.py [n_scenarios]   (stdlib only). Writes results to stdout."""
import json, random, sys, time, urllib.request, urllib.error

LUX = "sensor.living_room_presence_sensor_light_sensor_light_level"
RAD = "sensor.general_graz_meteo_station_global_radiation"
ELEV = "sensor.general_sun_solar_elevation"
LAMPS = {"top_left": "light.living_room_top_lamp_top_left", "top_right": "light.living_room_top_lamp_top_right",
         "bottom": "light.living_room_top_lamp_bottom", "desk": "light.work_station_lamp", "stick": "light.work_station_light_stick"}
KEYS = list(LAMPS)


class HA:
    def __init__(self, port, tokfile):
        self.base, self.tok = f"http://127.0.0.1:{port}", open(tokfile).read().strip()

    def req(self, m, p, body=None):
        r = urllib.request.Request(self.base + p, method=m, headers={"Authorization": "Bearer " + self.tok, "Content-Type": "application/json"},
                                   data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(r, timeout=60) as f:
                d = f.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{p}: HTTP {e.code} {e.read().decode()}")
        return json.loads(d) if d else None

    def call(self, svc, data=None):
        d, s = svc.split(".", 1); return self.req("POST", f"/api/services/{d}/{s}", data or {})

    def get(self, e):
        try:
            return self.req("GET", "/api/states/" + e)
        except RuntimeError:
            return {"state": "MISSING", "attributes": {}}

    def put(self, e, st, **a): return self.req("POST", "/api/states/" + e, {"state": str(st), "attributes": a})
    def state(self, e): return self.get(e)["state"]
    def pct(self, e):
        s = self.get(e)
        return None if s["state"] != "on" else int(round((s["attributes"].get("brightness") or 0) / 255 * 100))


A, B = HA(18123, "/tmp/hatest/token"), HA(18125, "/tmp/hatest_int/token")
RES = []


def check(tid, name, ok, detail=""):
    RES.append((tid, name, ok, detail)); print(f"[{'PASS' if ok else 'FAIL'}] {tid} {name} {detail}", flush=True)


def setnum(h, pid, v, b):  # A: input_number.*; B: number.light_k_<lamp> or the set_param service for zone parameters
    if b and not pid.startswith("light_k_"):
        h.call("adaptive_light.set_param", {"level": "zone", "target": "living_room", "name": pid.removeprefix("light_"), "value": v})
        return
    h.call(("number" if b else "input_number") + ".set_value", {"entity_id": f"{'number' if b else 'input_number'}.{pid}", "value": v})


def setswitch(h, pid, on, b):
    dom = "switch" if b else "input_boolean"
    h.call(f"{dom}.turn_{'on' if on else 'off'}", {"entity_id": f"{dom}.{pid}"})


def prepare():
    # A carries the user's 26 other automations (movie mode, vent mode, ...). They would move lamps in A only,
    # so everything except the plan's controller is switched off for this comparison.
    for st in A.req("GET", "/api/states"):
        e = st["entity_id"]
        if e.startswith("automation.") and e != "automation.light_adaptive_controller_shadow_by_default":
            A.call("automation.turn_off", {"entity_id": e})
    A.call("automation.turn_on", {"entity_id": "automation.light_adaptive_controller_shadow_by_default"})
    for h, b in ((A, False), (B, True)):
        h.call("light.turn_off", {"entity_id": list(LAMPS.values())})
        for k in KEYS:
            setswitch(h, f"light_{k}_auto", True, b)
        setswitch(h, "light_adaptive_enabled", False, b); setswitch(h, "light_adaptive_apply", True, b)
    B.call("adaptive_light.clear_pauses")
    B.call("switch.turn_off", {"entity_id": "switch.light_adaptive_scenarios"})   # A has its other automations off too


def apply_inputs(sc):
    for h, b in ((A, False), (B, True)):
        for k, v in sc["k"].items(): setnum(h, f"light_k_{k}", v, b)
        setnum(h, "light_dark_lux_offset", sc["offset"], b)
        setnum(h, "light_evening_start_hour", sc["evening_hour"], b)
        for pid, v in sc.get("extra", {}).items(): setnum(h, pid, v, b)
        h.put(LUX, sc["lux"]); h.put(RAD, sc["rad"]); h.put(ELEV, sc["elev"])
        h.call("input_select.select_option", {"entity_id": "input_select.living_room_light_mode", "option": sc["mode"]})
        h.call("input_select.select_option", {"entity_id": "input_select.work_station_desk_light_scene", "option": sc["scene"]})
        h.call("light.turn_off", {"entity_id": list(LAMPS.values())})
        for k, p in sc["lamps"].items():
            if p: h.call("light.turn_on", {"entity_id": LAMPS[k], "brightness_pct": p})
    time.sleep(0.5)
    for k, p in sc["lamps"].items():
        assert A.pct(LAMPS[k]) == B.pct(LAMPS[k]) == (p or None), (k, p, A.pct(LAMPS[k]), B.pct(LAMPS[k]))
    A.req("POST", "/api/events/light_openness_reset", {}); B.call("adaptive_light.openness_reset")
    B.call("adaptive_light.clear_pauses")
    time.sleep(1.2)
    A.call("homeassistant.update_entity", {"entity_id": "binary_sensor.light_evening_quiet"})
    time.sleep(1.2)


SENS = ["sensor.daylight_factor", "sensor.living_room_ambient_lux", "sensor.light_need", "sensor.light_ambient_trust",
        *[f"sensor.light_target_{k}" for k in KEYS]]


def scenario(rng):
    return dict(
        k={k: round(rng.uniform(0.05, 1.0), 3) for k in KEYS}, offset=rng.choice([0, 0.5, 2.0]),
        evening_hour=rng.choice([17, 23]), lux=rng.choice([0, 3, 20, 60, 150, 500]), rad=rng.choice([0, 40, 100, 300, 800]),
        elev=rng.choice([-20, 0, 25, 50]), mode=rng.choice(["Day", "Evening", "Movie", "Night", "Away"]),
        scene=rng.choice(["Work", "Bright", "Soft"]),
        lamps={k: rng.choice([None, None, 10, 35, 60, 90]) for k in KEYS},
    )


def main(n):
    rng = random.Random(42)
    prepare()
    bad = 0
    for i in range(n):
        sc = scenario(rng); apply_inputs(sc)
        diffs = []
        for e in SENS:
            a, b = A.state(e), B.state(e)
            try:
                same = abs(float(a) - float(b)) < 0.051 if "ambient" in e else float(a) == float(b)
            except ValueError:
                same = a == b
            if not same: diffs.append((e, a, b))
        ok = not diffs; bad += not ok
        check(f"S{i}", f"mode={sc['mode']} lux={sc['lux']} rad={sc['rad']} ev={sc['evening_hour']}", ok, str(diffs) if diffs else "")
    check("S*", f"sensor parity over {n} scenarios", bad == 0, f"{bad} mismatching")

    # controller: one tick each, compare resulting brightness per lamp
    bad = 0; moved = 0
    for i in range(10):
        sc = scenario(rng); sc["mode"] = rng.choice(["Day", "Evening", "Movie"]); sc["lamps"] = {k: rng.choice([5, 30, 55, 80, 100]) for k in KEYS}
        for attempt in range(3):
            apply_inputs(sc)
            while not 3 < time.localtime().tm_sec < 52: time.sleep(1)   # stay away from A's once-a-minute trigger
            setswitch(A, "light_adaptive_enabled", True, False); setswitch(B, "light_adaptive_enabled", True, True)
            A.call("automation.trigger", {"entity_id": "automation.light_adaptive_controller_shadow_by_default"})
            B.call("adaptive_light.controller_tick"); time.sleep(2.5)
            setswitch(A, "light_adaptive_enabled", False, False); setswitch(B, "light_adaptive_enabled", False, True)
            got_a = {k: A.pct(LAMPS[k]) for k in KEYS}; got_b = {k: B.pct(LAMPS[k]) for k in KEYS}
            before = sc["lamps"]
            # B has its own 60 s timer with unknown phase: a double step (> 10 points) means it ticked too, repeat the case
            if all(abs((got_a[k] or 0) - before[k]) <= 10 and abs((got_b[k] or 0) - before[k]) <= 10 for k in KEYS): break
        moved += sum(1 for k in KEYS if got_a[k] != before[k])
        ok = got_a == got_b; bad += not ok
        check(f"C{i}", f"controller tick mode={sc['mode']}", ok, f"A={got_a} B={got_b} before={before}")
    check("C*", "controller parity (10 ticks, apply on)", bad == 0 and moved > 10, f"mismatch={bad}, lamps moved on A={moved}")
    ctrl_ok = bad == 0

    # pause: manual change in B sets pause, controller in B then leaves the lamp alone; own change does not set a pause
    print("waiting 62 s so the controller's own-change window (60 s) has passed", flush=True); time.sleep(62)
    B.call("adaptive_light.clear_pauses"); time.sleep(0.5)
    B.call("light.turn_on", {"entity_id": LAMPS["desk"], "brightness_pct": 77}); time.sleep(1.5)
    p = B.state("sensor.light_desk_pause_until")
    check("O1", "manual change pauses lamp (B)", p not in ("unknown", "unavailable", "MISSING"), p)
    B.call("adaptive_light.clear_pauses"); setswitch(B, "light_adaptive_enabled", True, True)
    B.call("adaptive_light.controller_tick"); time.sleep(2); setswitch(B, "light_adaptive_enabled", False, True)
    # the controller changed lamps with its own context: no pause for any lamp
    ps = {k: B.state(f"sensor.light_{k}_pause_until") for k in KEYS}
    check("O2", "controller's own changes do not start a pause (B)", all(v in ("unknown", "unavailable") for v in ps.values()), str(ps))
    print(open("/dev/null").read(), end="")
    return RES


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    res = main(n)
    fails = [r for r in res if not r[2]]
    print(f"\n{len(res)-len(fails)}/{len(res)} PASS")
    sys.exit(1 if fails else 0)

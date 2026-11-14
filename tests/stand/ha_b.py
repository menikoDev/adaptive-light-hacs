"""REST helpers for stands A (18123, YAML plan) and B (18125, integration). stdlib only."""
import json, time, urllib.request, urllib.error

BASELINE = [
    ("media_player.living_room_tv", "off"), ("binary_sensor.living_room_presence_sensor", "on"),
    ("binary_sensor.living_room_presence_sensor_sofa_zone", "off"), ("binary_sensor.living_room_presence_right_table_zone", "off"),
    ("binary_sensor.living_room_presence_sensor_refrigerator_zone", "off"), ("binary_sensor.living_room_presence_sensor_entrance_zone", "off"),
    ("binary_sensor.living_room_presence_sensor_carpet_zone", "off"),
    ("sensor.living_room_presence_sensor_light_sensor_light_level", "100"), ("sensor.general_graz_meteo_station_global_radiation", "600"),
    ("sensor.general_sun_solar_elevation", "30"), ("sensor.general_graz_meteo_station_window_open_advice", "none"),
    ("sensor.living_room_left_window_open_percentage", "0"), ("sensor.living_room_right_window_open_percentage", "0"),
    ("sensor.bedroom_lux", "20"), ("binary_sensor.bedroom_presence", "off"),
]
LAMPS = {"top_left": "light.living_room_top_lamp_top_left", "top_right": "light.living_room_top_lamp_top_right",
         "bottom": "light.living_room_top_lamp_bottom", "desk": "light.work_station_lamp", "stick": "light.work_station_light_stick"}


class HA:
    def __init__(self, port=18125, tokfile="/tmp/hatest_int/token"):
        self.base, self.tok = f"http://127.0.0.1:{port}", open(tokfile).read().strip()

    def req(self, m, p, body=None):
        r = urllib.request.Request(self.base + p, method=m, headers={"Authorization": "Bearer " + self.tok, "Content-Type": "application/json"},
                                   data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(r, timeout=90) as f:
                d = f.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{m} {p}: HTTP {e.code} {e.read().decode()[:600]}")
        return json.loads(d) if d else None

    def call(self, svc, data=None, ret=False):
        d, s = svc.split(".", 1)
        return self.req("POST", f"/api/services/{d}/{s}" + ("?return_response" if ret else ""), data or {})

    def get(self, e):
        try:
            return self.req("GET", "/api/states/" + e)
        except RuntimeError:
            return {"state": "MISSING", "attributes": {}}

    def state(self, e): return self.get(e)["state"]
    def attr(self, e, a): return self.get(e)["attributes"].get(a)
    def put(self, e, st, **a): return self.req("POST", "/api/states/" + e, {"state": str(st), "attributes": a})
    def seed(self):
        for e, s in BASELINE:
            self.put(e, s)

    def pct(self, e):
        s = self.get(e)
        return None if s["state"] != "on" else int(round((s["attributes"].get("brightness") or 0) / 255 * 100))

    def lamp(self, e, on=True, pct=None, **kw):
        if not on:
            return self.call("light.turn_off", {"entity_id": e})
        return self.call("light.turn_on", {"entity_id": e, **({"brightness_pct": pct} if pct is not None else {}), **kw})

    # config flow over REST
    def flow_start(self, handler="adaptive_light"):
        return self.req("POST", "/api/config/config_entries/flow", {"handler": handler})

    def flow_step(self, fid, data):
        return self.req("POST", f"/api/config/config_entries/flow/{fid}", data)

    def entries(self, domain="adaptive_light"):
        return [e for e in self.req("GET", "/api/config/config_entries/entry") if e["domain"] == domain]

    def options_start(self, entry_id):
        return self.req("POST", "/api/config/config_entries/options/flow", {"handler": entry_id})

    def options_step(self, fid, data):
        return self.req("POST", f"/api/config/config_entries/options/flow/{fid}", data)

    def wait(self, fn, timeout=60, step=0.5):
        t = time.time()
        while time.time() - t < timeout:
            v = fn()
            if v:
                return v
            time.sleep(step)
        return None

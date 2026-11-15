"""Lovelace dashboard (YAML mode) for the integration, generated from a config: one view per zone plus an overview.

python tools/gen_dashboard.py [config.json] > dashboard/adaptive-light-dashboard.yaml
Without an argument the YAML plan's living room preset is used. Entity ids come from ids.py, so they always match what the
integration creates."""
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import conftest  # noqa: F401,E402  (loads the pure package without Home Assistant)
from adaptive_light import ids  # noqa: E402
from adaptive_light.model import Config, plan_living_room  # noqa: E402


def zone_view(cfg: Config, z: str) -> dict:
    pre = ids.prefix(cfg, z)
    zi = ids.zone_sensor_ids(cfg, z)
    lamps = cfg.lamps_in_zone(z)
    cards = [
        {"type": "entities", "title": f"{cfg.zones[z]['name']}: pipeline", "entities": [
            f"sensor.{zi['ambient']}", f"sensor.{zi['need']}", f"sensor.{zi['trust']}", f"sensor.{zi['openness']}",
            f"sensor.{zi['openness_raw']}", f"binary_sensor.{pre}light_evening_quiet", "sensor.daylight_factor"]},
        {"type": "history-graph", "title": "Ambient, need, trust", "hours_to_show": 12, "entities": [
            f"sensor.{zi['ambient']}", f"sensor.{zi['need']}", f"sensor.{zi['trust']}", f"sensor.{zi['openness']}"]},
        {"type": "entities", "title": "Lamps: target and automatic control", "entities": [
            e for k in lamps for e in (
                {"type": "section", "label": cfg.lamps[k]["name"]},
                f"sensor.light_target_{k}", f"switch.light_{k}_auto", f"sensor.light_{k}_pause_until", f"number.light_k_{k}")]},
    ]
    return {"title": cfg.zones[z]["name"], "path": z, "icon": "mdi:lightbulb-group", "cards": cards}


def overview(cfg: Config) -> dict:
    return {"title": "Adaptive light", "path": "overview", "icon": "mdi:brightness-auto", "cards": [
        {"type": "entities", "title": "Control", "entities": [
            "switch.light_adaptive_enabled", "switch.light_adaptive_apply", "switch.light_adaptive_scenarios", "sensor.daylight_factor",
            {"type": "button", "name": "Clear all pauses", "tap_action": {"action": "perform-action", "perform_action": "adaptive_light.clear_pauses"}},
            {"type": "button", "name": "Reset openness", "tap_action": {"action": "perform-action", "perform_action": "adaptive_light.openness_reset"}},
            {"type": "button", "name": "Run one controller tick", "tap_action": {"action": "perform-action", "perform_action": "adaptive_light.controller_tick"}}]},
        {"type": "markdown", "content": "**Shadow mode:** enabled on, apply off: the controller only writes to the logbook what it would do. "
                                         "Switch apply on to let it move the lamps (10 % per minute). Each lamp has its own auto switch."},
        {"type": "logbook", "title": "Adaptive light (shadow) log", "hours_to_show": 6, "entities": ["sensor.daylight_factor"]}]}


def build(raw: dict | None = None) -> dict:
    cfg = Config(raw or plan_living_room())
    return {"title": "Adaptive light", "views": [overview(cfg)] + [zone_view(cfg, z) for z in cfg.zone_order]}


def entities_used(dash: dict) -> set[str]:
    out = set()

    def walk(x):
        if isinstance(x, str) and "." in x and not x.startswith(("mdi:", "adaptive_light.")) and " " not in x:
            out.add(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ("entities", "entity"):
                    walk(v)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(dash)
    return out


if __name__ == "__main__":
    raw = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else None
    sys.stdout.write(yaml.safe_dump(build(raw), sort_keys=False, allow_unicode=True, width=120))

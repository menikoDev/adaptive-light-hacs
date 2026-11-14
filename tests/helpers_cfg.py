"""Two-room config used by the model, manager and flow tests."""
import copy

import conftest  # noqa: F401
from adaptive_light import flow_ops as ops
from adaptive_light.model import plan_living_room


def two_rooms():
    cfg = copy.deepcopy(plan_living_room())
    ops.add_zone(cfg, "Bedroom")
    ops.set_sensor(cfg, "illuminance", "sensor.bedroom_lux", "bedroom")
    ops.set_sensor(cfg, "presence", "binary_sensor.bedroom_presence", "bedroom")
    ops.add_lamps(cfg, "bedroom", ["light.bed_ceiling", "light.bed_night"])
    ops.add_group(cfg, "bedroom", "All bed", ["bed_ceiling", "bed_night"], share_pause=True)
    return cfg

"""The YAML plan's living room expressed through the new model: parameter defaults, lamp keys and entity ids."""
import conftest  # noqa: F401  (loads the pure package)
from adaptive_light.model import Config, plan_living_room

CFG = Config(plan_living_room())
ZONE = "living_room"
LAMP_KEYS = list(CFG.lamps_in_zone(ZONE))
DEFAULT_LAMP_ENTITIES = {k: CFG.entity_of(k) for k in LAMP_KEYS}
DEFAULTS = {k: v for k, v in CFG.flat(ZONE).items() if isinstance(v, (int, float))}

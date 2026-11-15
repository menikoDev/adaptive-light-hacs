"""Constants. Parameters and their defaults live in params.py; the configuration model in model.py."""
DOMAIN = "adaptive_light"
CONF_CONFIG = "config"
PLATFORMS = ["sensor", "binary_sensor", "switch", "number"]
SERVICES = ("controller_tick", "clear_pauses", "openness_reset", "set_param")

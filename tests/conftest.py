import os, sys, types, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_pure_package():
    """Import adaptive_light modules without running __init__.py."""
    pkg = types.ModuleType("adaptive_light")
    pkg.__path__ = [os.path.join(ROOT, "custom_components", "adaptive_light")]
    sys.modules["adaptive_light"] = pkg
    for name in ("model", "control", "persistence", "manager", "ids"):
        spec = importlib.util.spec_from_file_location(
            f"adaptive_light.{name}", os.path.join(pkg.__path__[0], f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"adaptive_light.{name}"] = mod
        spec.loader.exec_module(mod)


_load_pure_package()

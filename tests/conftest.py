import os, re, sys, importlib.util, types
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.environ.get("PLAN", "/cursor/stores/self/docs/adaptive-light-plan.md")


def _load_pure_package():
    """Import adaptive_light.{const,core,calibration,manager} WITHOUT running __init__.py (it needs Home Assistant)."""
    pkg = types.ModuleType("adaptive_light")
    pkg.__path__ = [os.path.join(ROOT, "custom_components", "adaptive_light")]
    sys.modules["adaptive_light"] = pkg
    for name in ("params", "model", "core", "calibration", "persistence", "executors", "exec_runtime", "manager", "ids", "flow_ops", "services", "areas", "draft"):
        spec = importlib.util.spec_from_file_location(f"adaptive_light.{name}", os.path.join(pkg.__path__[0], f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"adaptive_light.{name}"] = mod
        spec.loader.exec_module(mod)


_load_pure_package()


@pytest.fixture(scope="session")
def plan_blocks():
    text = open(PLAN, encoding="utf-8").read()
    return re.findall(r"```(\w+)\n(.*?)```", text, re.S)

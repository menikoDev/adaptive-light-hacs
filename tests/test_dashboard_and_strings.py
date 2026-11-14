import json, pathlib, sys, yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_dashboard, gen_strings  # noqa: E402
from adaptive_light import ids, params as P  # noqa: E402
from adaptive_light.model import Config  # noqa: E402
from helpers_cfg import two_rooms  # noqa: E402


def test_dashboard_only_references_entities_the_integration_creates():
    for raw in (None, two_rooms()):
        cfg = Config(raw or __import__("adaptive_light.model", fromlist=["x"]).plan_living_room())
        known = {e for v in ids.expected_entities(cfg).values() for e in v}
        dash = gen_dashboard.build(raw)
        assert [v["path"] for v in dash["views"]] == ["overview"] + cfg.zone_order
        assert gen_dashboard.entities_used(dash) <= known, gen_dashboard.entities_used(dash) - known


def test_committed_dashboard_is_current():
    assert yaml.safe_load(open(ROOT / "dashboard" / "adaptive-light-dashboard.yaml")) == yaml.safe_load(yaml.safe_dump(gen_dashboard.build(None)))


def test_translations_cover_every_parameter_and_are_current():
    base = ROOT / "custom_components" / "adaptive_light"
    for i, name in enumerate(("en", "ru")):
        built = gen_strings.build(i)
        assert json.load(open(base / "translations" / f"{name}.json")) == built
        labels = built["config"]["step"]["params"]["data"]
        for level_names in P.UI_NAMES.values():
            assert set(level_names) <= set(labels)
    assert json.load(open(base / "strings.json")) == gen_strings.build(0)

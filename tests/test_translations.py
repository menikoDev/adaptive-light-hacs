"""Every user-facing string has the same key in English and Russian."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "adaptive_light"
FILES = {
    "strings": ROOT / "strings.json",
    "en": ROOT / "translations" / "en.json",
    "ru": ROOT / "translations" / "ru.json",
}


def _flatten(data, prefix=""):
    out = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


def _load(name):
    return json.loads(FILES[name].read_text(encoding="utf-8"))


def test_every_language_has_the_same_keys_and_russian_is_not_english():
    strings, en, ru = (_flatten(_load(name)) for name in ("strings", "en", "ru"))
    assert set(strings) == set(en) == set(ru)
    assert strings == en
    same = sorted(key for key, value in ru.items() if value == en[key])
    assert same == []

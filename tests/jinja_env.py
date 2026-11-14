"""Minimal emulation of HA's template environment, enough to render the plan's own macros and templates."""
import ast, datetime as dt
import jinja2

_cur = {"ha": None}  # imported macro modules cache their globals, so globals must look the active FakeHA up lazily


def _g(name):
    return lambda *a: getattr(_cur["ha"], "_" + name)(*a)


class FakeHA:
    _envs = {}
    _tpls = {}

    def __init__(self):
        self.s = {}
        self.a = {}
        self.now = dt.datetime(2026, 11, 13, 12, 0, tzinfo=dt.timezone.utc)

    def put(self, e, state, **attrs):
        self.s[e] = None if state is None else str(state)
        self.a[e] = attrs

    def _states(self, e):
        v = self.s.get(e)
        return v if v is not None else ("unavailable" if e in self.s else "unknown")

    def _is_state(self, e, v):
        return self.s.get(e) in v if isinstance(v, list) else self.s.get(e) == v

    def _state_attr(self, e, a):
        return self.a.get(e, {}).get(a)

    def _has_value(self, e):
        return self.s.get(e) not in (None, "unknown", "unavailable")

    def _now(self):
        return self.now

    def env(self, macro_src=None):
        if macro_src not in self._envs:
            env = jinja2.Environment(loader=jinja2.DictLoader({"adaptive_light.jinja": macro_src or ""}),
                                     undefined=jinja2.StrictUndefined)
            for n in ("states", "is_state", "state_attr", "has_value", "now"):
                env.globals[n] = _g(n)
            def is_number(v):
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return False
                return f == f and f not in (float("inf"), float("-inf"))

            env.globals["as_timestamp"] = lambda v, d=None: v.timestamp() if hasattr(v, "timestamp") else (0 if d is None else d)
            env.filters["is_number"] = is_number
            self._envs[macro_src] = env
        return self._envs[macro_src]

    def render(self, template, macro_src=None, **ctx):
        _cur["ha"] = self
        env = self.env(macro_src)
        key = (macro_src, template)
        if key not in self._tpls:
            self._tpls[key] = env.from_string(template)
        return self._tpls[key].render(**ctx).strip()


def native(s):
    try:
        return ast.literal_eval(s)
    except Exception:
        return s

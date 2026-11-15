"""A momentary action paints a lamp, then the timer restores it or turns it off."""
import asyncio

from adaptive_light.momentary import MomentaryActions


class _State:
    def __init__(self, state, **attributes):
        self.state = state
        self.attributes = attributes


class _States:
    def __init__(self):
        self._data = {}

    def get(self, entity):
        return self._data.get(entity)

    def set(self, entity, state, **attributes):
        self._data[entity] = _State(state, **attributes)


class _Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, dict(data)))


class _Later:
    def __init__(self):
        self.jobs = []

    def __call__(self, hass, delay, action):
        self.jobs.append((delay, action))

        def cancel():
            self.jobs = [job for job in self.jobs if job[1] is not action]

        return cancel


class _Home:
    def __init__(self):
        self.states = _States()
        self.services = _Services()


def run(coro):
    return asyncio.run(coro)


def _config(mode):
    return {
        "zones": [],
        "momentary_actions": [{
            "key": "fridge",
            "lamp": "light.kitchen",
            "color": "white",
            "duration": 10,
            "return": mode,
        }],
    }


def test_trigger_applies_the_configured_color():
    home, later = _Home(), _Later()
    home.states.set("light.kitchen", "on", brightness=80, color_mode="hs", hs_color=(30, 40))
    actions = MomentaryActions(home, _config("restore"), call_later=later)
    assert run(actions.trigger("fridge")) is True
    assert home.services.calls == [("light", "turn_on", {"entity_id": "light.kitchen", "color_name": "white"})]
    assert actions.active["fridge"]["brightness"] == 80
    assert actions.active["fridge"]["hs_color"] == (30, 40)
    assert later.jobs[0][0] == 10


def test_timer_restore_returns_the_prior_state():
    home, later = _Home(), _Later()
    home.states.set("light.kitchen", "on", brightness=80, color_mode="hs", hs_color=(30, 40))
    actions = MomentaryActions(home, _config("restore"), call_later=later)
    run(actions.trigger("fridge"))
    home.states.set("light.kitchen", "on", brightness=255, color_mode="rgb", rgb_color=(255, 255, 255))
    home.services.calls.clear()
    run(later.jobs[0][1](None))
    assert home.services.calls == [(
        "light", "turn_on", {"entity_id": "light.kitchen", "brightness": 80, "hs_color": (30, 40)},
    )]
    assert "fridge" not in actions.active


def test_timer_off_turns_the_lamp_off():
    home, later = _Home(), _Later()
    home.states.set("light.kitchen", "on", brightness=80, color_mode="hs", hs_color=(30, 40))
    actions = MomentaryActions(home, _config("off"), call_later=later)
    run(actions.trigger("fridge"))
    home.services.calls.clear()
    run(later.jobs[0][1](None))
    assert home.services.calls == [("light", "turn_off", {"entity_id": "light.kitchen"})]
    assert "fridge" not in actions.active

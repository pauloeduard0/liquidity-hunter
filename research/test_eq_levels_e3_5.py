"""Memory estimator handles sharing, cycles and Pydantic state without mutation."""

import copy
import sys

from research.eq_levels_e1 import Replay
from research.eq_levels_e3_5 import measure, retained_bytes
from research.test_eq_levels_e2 import candles


def test_shared_references_are_counted_once():
    shared = bytearray(1000)
    value = [shared, shared]
    assert retained_bytes(value) == sys.getsizeof(value) + sys.getsizeof(shared)


def test_cycle_terminates():
    value = []
    value.append(value)
    assert retained_bytes(value) == sys.getsizeof(value)


def test_model_slots_are_included():
    class State:
        __slots__ = ("payload",)

    state = State()
    state.payload = bytearray(1000)
    assert retained_bytes(state) == sys.getsizeof(state) + sys.getsizeof(state.payload)


def test_measurement_preserves_replay_and_observes_growth():
    run = Replay()
    bars = candles(320)
    for bar in bars[:160]:
        run.feed(bar)
    first = measure(run)
    for bar in bars[160:]:
        run.feed(bar)
    before = copy.deepcopy(run.__dict__)
    last = measure(run)
    assert run.__dict__ == before
    assert last["retained_python_bytes"] > first["retained_python_bytes"]
    assert last["checkpoint_json_bytes"] > first["checkpoint_json_bytes"]
    assert last["retained_python_bytes"] > last["state_without_output_logs_bytes"]
    assert last["snapshots"] == 320

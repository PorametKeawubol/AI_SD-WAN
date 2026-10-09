import itertools

import pytest
from pydantic import ValidationError

from sdwan.domain import LINKS, PROFILES, Telemetry
from sdwan.policy import Controller


def sample(link, now=100, **changes):
    return Telemetry.model_validate(
        {
            "link_id": link,
            "timestamp": now,
            "up": True,
            "latency_ms": 25,
            "jitter_ms": 5,
            "loss_pct": 0.1,
            "utilization_pct": 30,
            **changes,
        }
    )


@pytest.mark.parametrize("states", list(itertools.product([False, True], repeat=3)))
def test_exhaustive_up_down_combinations(states):
    controller = Controller(PROFILES["voice"])
    controller.last_switch = 99  # failures must bypass active cooldown
    data = {link: sample(link, up=up) for link, up in zip(LINKS, states)}
    decision = controller.propose(data, 100)
    if not any(states):
        assert decision.action == "no_path" and decision.target is None
    elif not states[0]:
        assert decision.action == "switch" and data[decision.target].up
    else:
        assert decision.action == "hold" and decision.target == "wan1"


def test_stale_and_future_links_are_ineligible():
    controller = Controller(PROFILES["voice"])
    data = {"wan1": sample("wan1", now=89), "wan2": sample("wan2"), "wan3": sample("wan3", now=101)}
    decision = controller.propose(data, 100)
    assert decision.action == "switch" and decision.target == "wan2"


@pytest.mark.parametrize(
    "metric,value",
    [
        ("latency_ms", float("nan")),
        ("jitter_ms", -1),
        ("loss_pct", 101),
        ("utilization_pct", float("inf")),
        ("up", "false"),
    ],
)
def test_invalid_telemetry_is_rejected(metric, value):
    with pytest.raises(ValidationError):
        sample("wan1", **{metric: value})


def test_sla_violation_escapes_even_during_cooldown():
    controller = Controller(PROFILES["voice"])
    controller.last_switch = 99
    decision = controller.propose({"wan1": sample("wan1", loss_pct=8), "wan2": sample("wan2")}, 100)
    assert decision.action == "switch" and decision.target == "wan2"


def test_prediction_cannot_choose_sla_bad_path_when_compliant_one_exists():
    controller = Controller(PROFILES["voice"])
    data = {"wan1": sample("wan1"), "wan2": sample("wan2", loss_pct=1.1)}
    # A pessimistic forecast must not make a currently bad path preferable to a healthy path.
    decision = controller.propose(data, 100, {"wan1": [1000, 200, 100, 100]})
    assert decision.target == "wan1"


def test_confirmation_requires_new_samples_and_elapsed_time():
    controller = Controller(PROFILES["voice"])
    data = {
        "wan1": sample("wan1", latency_ms=95, jitter_ms=25, loss_pct=0.8),
        "wan2": sample("wan2"),
    }
    assert controller.propose(data, 100).action == "hold"
    for _ in range(10):
        assert controller.propose(data, 100).action == "hold"
    assert controller.propose(data, 105).action == "hold"  # no new sample
    data = {key: value.model_copy(update={"timestamp": 105}) for key, value in data.items()}
    assert controller.propose(data, 105).action == "hold"
    data = {key: value.model_copy(update={"timestamp": 110}) for key, value in data.items()}
    assert controller.propose(data, 110).action == "switch"
    assert controller.current == "wan1"  # proposed, not acknowledged


def test_nan_forecast_falls_back_to_current_quality():
    controller = Controller(PROFILES["voice"])
    data = {"wan1": sample("wan1"), "wan2": sample("wan2", latency_ms=40)}
    decision = controller.propose(data, 100, {"wan1": [float("nan"), 0, 0, 0]})
    assert decision.target == "wan1" and not decision.predicted

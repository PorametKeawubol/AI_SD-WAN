"""Recorded-data contract fixtures; these do not claim measured lab accuracy."""

import pytest

from lab.evaluate_forecast import evaluation_windows, measured_series
from sdwan.domain import Telemetry


def sample(at, **values):
    return Telemetry(
        link_id="wan1",
        timestamp=at,
        up=True,
        latency_ms=at / 10,
        jitter_ms=3,
        loss_pct=0,
        utilization_pct=10,
    ).model_copy(update=values)


def test_api_snapshots_are_deduplicated_without_using_todays_clock():
    row = {**sample(1000).model_dump(), "fresh": True, "age_seconds": 2}
    result = measured_series([{"state": {"telemetry": {"wan1": row}}}] * 3)
    assert len(result["wan1"]) == 1
    conflict = {**row, "loss_pct": 2}
    with pytest.raises(ValueError, match="Conflicting"):
        measured_series(
            [
                {"state": {"telemetry": {"wan1": row}}},
                {"state": {"telemetry": {"wan1": conflict}}},
            ]
        )


def test_future_targets_never_change_current_features_and_never_cross_trials():
    series = [sample(1000 + i * 5) for i in range(22)]
    windows, _ = evaluation_windows(series, "strict")
    assert windows[0]["current_timestamp"] == 1055
    assert windows[0]["target_timestamp"] == 1085
    altered = [
        row.model_copy(update={"latency_ms": 999}) if row.timestamp >= 1085 else row
        for row in series
    ]
    changed, _ = evaluation_windows(altered, "strict")
    assert changed[0]["matrix"].tolist() == windows[0]["matrix"].tolist()
    assert changed[0]["target"][0] == 999
    first_trial, counters = evaluation_windows(series[:12], "strict")
    assert not first_trial and counters["future_target_missing"] == 1


def test_missing_future_target_is_not_interpolated_or_replaced_with_zero():
    series = [sample(1000 + i * 5) for i in range(22) if i != 17]
    windows, counters = evaluation_windows(series, "strict")
    assert counters["future_target_missing"] > 0
    assert all(row["current_timestamp"] != 1055 for row in windows)


def test_down_target_keeps_measured_loss_but_excludes_rtt_sentinels():
    series = [sample(1000 + i * 5) for i in range(18)]
    series[17] = series[17].model_copy(
        update={
            "up": False,
            "latency_ms": 10000,
            "jitter_ms": 5000,
            "loss_pct": 100,
        }
    )
    windows, counters = evaluation_windows(series, "strict")
    assert windows[0]["available_metrics"] == [False, False, True, True]
    assert windows[0]["target"][2] == 100 and counters["future_down_windows"] == 1

"""Sampling fixtures test causality/gap rejection, not real model accuracy."""

import numpy as np
import pytest

from sdwan.domain import WINDOW, Telemetry
from sdwan.features import features, regular_history, resample_live_history
from sdwan.service import Service


def series(jitter=False):
    rows = []
    for index in range(WINDOW + 1):
        timestamp = 1000 + index * 5 + (0.12 if jitter and index % 2 else 0)
        rows.append(
            Telemetry(
                link_id="wan1",
                timestamp=timestamp,
                up=True,
                latency_ms=(timestamp - 1000) * 2 + 20,
                jitter_ms=3,
                loss_pct=0,
                utilization_pct=10,
            )
        )
    return rows


def test_regular_grid_preserves_existing_features_and_original_observations():
    rows = series()
    original = [row.model_dump() for row in rows]
    normalized = resample_live_history(rows)
    assert regular_history(normalized)
    np.testing.assert_allclose(features(normalized), features(rows))
    assert [row.model_dump() for row in rows] == original


def test_bounded_jitter_interpolation_uses_only_bracketed_observations():
    rows = series(jitter=True)
    assert not regular_history(rows)
    normalized = resample_live_history(rows)
    assert normalized is not None and len(normalized) == WINDOW
    assert regular_history(normalized)
    assert normalized[0].timestamp >= rows[0].timestamp
    assert normalized[-1].timestamp == rows[-1].timestamp
    for sample in normalized:
        assert sample.latency_ms == pytest.approx((sample.timestamp - 1000) * 2 + 20)
    assert rows[1].timestamp == 1005.12  # Recorded measurements are never relabeled.


@pytest.mark.parametrize("fault", ["gap", "down", "duplicate", "foreign", "short"])
def test_sampling_does_not_fill_missing_down_or_invalid_history(fault):
    rows = series()
    if fault == "gap":
        rows[5] = rows[5].model_copy(update={"timestamp": rows[5].timestamp + 2})
    elif fault == "down":
        rows[5] = rows[5].model_copy(update={"up": False})
    elif fault == "duplicate":
        rows[5] = rows[4]
    elif fault == "foreign":
        rows[5] = rows[5].model_copy(update={"link_id": "wan2"})
    else:
        rows = rows[:11]
    assert resample_live_history(rows) is None


def test_live_sampling_is_explicit_and_stores_measured_timestamps(tmp_path):
    class ObservingModel:
        def predict(self, history):
            assert regular_history(history)
            return history[-1].vector()

    service = Service(tmp_path / "live.sqlite3", forecast_sampling="causal_linear_5s")
    service.model = ObservingModel()
    rows = series(jitter=True)
    for row in rows:
        service.ingest([row.model_dump()], now=row.timestamp)
    assert len(service.histories["wan1"]) == 13
    assert service.forecasts()["wan1"] == rows[-1].vector()
    assert service.store.history("wan1", 13)[1]["timestamp"] == 1005.12
    assert service.status(rows[-1].timestamp)["forecast_sampling"] == "causal_linear_5s"
    default = Service(tmp_path / "default.sqlite3")
    assert default.forecast_sampling == "strict" and default.histories["wan1"].maxlen == WINDOW

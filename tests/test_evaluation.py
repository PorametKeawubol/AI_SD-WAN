import numpy as np

from sdwan.domain import LINKS, PROFILES
from sdwan.evaluation import bootstrap_interval, observed_inputs, trial
from sdwan.features import features, regular_history
from sdwan.simulator import NORMAL, SCENARIOS, trace, training_episode


class PersistenceModel:
    def predict_matrix(self, matrix):
        return matrix[:, [0, 6, 12, 18]]


def test_seed_reproducibility_and_case_count():
    assert len(SCENARIOS) == 16 and len({s.id for s in SCENARIOS}) == 16
    assert [f.observed for f in trace(SCENARIOS[11], 50000)] == [
        f.observed for f in trace(SCENARIOS[11], 50000)
    ]


def test_history_features_are_causal_and_gap_disables_prediction():
    history = training_episode(1000, 0)["wan1"]
    matrix = features(history[:12])
    future = history[12].model_copy(update={"latency_ms": 9999})
    history[12] = future
    assert np.array_equal(matrix, features(history[:12]))
    assert regular_history(history[:12])
    history[5] = history[5].model_copy(update={"timestamp": history[5].timestamp + 1})
    assert not regular_history(history[:12])


def test_all_down_safe_handling_is_zero_connectivity():
    frames = trace(SCENARIOS[6], 50000)
    snapshots, forecasts, _, _ = observed_inputs(frames, PersistenceModel())
    result = trial(frames, snapshots, forecasts, PROFILES["voice"], "predictive")
    assert result["passed"]
    assert result["availability"] == 0 and result["sla_attainment"] is None
    assert result["all_down_detection_seconds"] == 0


def test_priority_cannot_hide_unrecovered_brownout():
    frames = trace(SCENARIOS[7], 50000)
    snapshots, forecasts, _, _ = observed_inputs(frames, PersistenceModel())
    result = trial(frames, snapshots, forecasts, PROFILES["voice"], "priority")
    assert not result["passed"] and result["sla_attainment"] == 0
    assert result["max_recovery_seconds"] > 15


def test_corrupt_rows_are_quarantined_without_future_peeking():
    frames = trace(SCENARIOS[15], 50000)
    snapshots, _, _, count = observed_inputs(frames, PersistenceModel())
    assert count > 0
    for tick, frame in enumerate(frames):
        for link in LINKS:
            assert snapshots[tick][link].timestamp <= frame.now
    assert all(snapshots[t]["wan1"].loss_pct <= 100 for t in range(len(frames)))


def test_normal_control_stays_stable():
    frames = trace(NORMAL, 50000)
    snapshots, forecasts, _, _ = observed_inputs(frames, PersistenceModel())
    result = trial(frames, snapshots, forecasts, PROFILES["voice"], "predictive")
    assert result["passed"] and result["switches"] == 0


def test_bootstrap_interval_reproducible_and_paired():
    assert bootstrap_interval([0.01, 0.02, 0.03]) == bootstrap_interval([0.01, 0.02, 0.03])
    assert bootstrap_interval([0] * 30) == [0, 0]

"""Artifact contracts for optional residual shrinkage and preserved frozen bundles."""

import json

import numpy as np
import pytest

from sdwan import model
from sdwan.domain import HORIZON, METRICS
from sdwan.features import FEATURE_NAMES


class ResidualModel:
    def __init__(self, **kwargs):
        self.calls = 0

    def load_model(self, path):
        pass

    def predict(self, matrix):
        self.calls += 1
        return np.full(len(matrix), 20, dtype=np.float32)


def bundle(tmp_path, monkeypatch, weights=None):
    metadata = {
        "schema": model.MODEL_SCHEMA,
        "feature_names": FEATURE_NAMES,
        "horizon_steps": HORIZON,
        "enabled_metrics": {metric: metric != "loss_pct" for metric in METRICS},
    }
    if weights is not None:
        metadata["blend_weights"] = weights
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(model, "XGBRegressor", ResidualModel)
    return model.Forecaster(tmp_path)


def matrix():
    rows = np.zeros((2, len(FEATURE_NAMES)), dtype=np.float32)
    rows[:, [0, 6, 12, 18]] = [10, 4, 1, 30]
    return rows


def test_legacy_artifact_has_unchanged_full_residual_and_persistence_behavior(
    tmp_path, monkeypatch
):
    forecaster = bundle(tmp_path, monkeypatch)
    np.testing.assert_array_equal(forecaster.predict_matrix(matrix()), [[30, 24, 1, 50]] * 2)
    assert forecaster.models[2].calls == 0


def test_blending_shrinks_residual_without_changing_current_values_or_mask(tmp_path, monkeypatch):
    weights = dict(zip(METRICS, (0.25, 0.5, 0, 0.75)))
    forecaster = bundle(tmp_path, monkeypatch, weights)
    source = matrix()
    before = source.copy()
    np.testing.assert_array_equal(forecaster.predict_matrix(source), [[15, 14, 1, 45]] * 2)
    np.testing.assert_array_equal(source, before)
    assert forecaster.models[2].calls == 0


@pytest.mark.parametrize("weight", [-0.1, 1.1, True, "0.5", float("nan"), float("inf")])
def test_corrupted_blend_weights_are_rejected_before_model_load(tmp_path, monkeypatch, weight):
    weights = dict(zip(METRICS, (weight, 1, 0, 1)))
    with pytest.raises(ValueError, match="blend weights"):
        bundle(tmp_path, monkeypatch, weights)


@pytest.mark.parametrize("weights", [[], {"latency_ms": 1}, dict(zip(METRICS, (1, 1, 0.5, 1)))])
def test_weights_require_complete_metric_contract_and_enabled_agreement(
    tmp_path, monkeypatch, weights
):
    with pytest.raises(ValueError, match="blend weights"):
        bundle(tmp_path, monkeypatch, weights)


def test_empty_batch_and_invalid_input_do_not_call_models(tmp_path, monkeypatch):
    forecaster = bundle(tmp_path, monkeypatch)
    assert forecaster.predict_matrix(np.empty((0, 26))).shape == (0, 4)
    for values in (np.zeros((2, 25)), np.zeros(26), np.full((1, 26), np.nan)):
        with pytest.raises(ValueError, match="finite matrix"):
            forecaster.predict_matrix(values)
    assert all(member.calls == 0 for member in forecaster.models)


def test_old_train_command_preserves_existing_bundle_before_training(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text("existing evidence")
    with pytest.raises(ValueError, match="Existing model preserved"):
        model.train(tmp_path, train_episodes=1, validation_episodes=1)
    assert path.read_text() == "existing evidence"

"""Leakage, serialization and isolated packet-campaign contracts."""

import copy
import json

import numpy as np
import pytest
from test_optimization import recorded_document

from lab import link_forecast_campaign as campaign
from sdwan.domain import HORIZON, METRICS
from sdwan.features import FEATURE_NAMES
from sdwan.forecast_search import select_candidate, train_search
from sdwan.linear import LinearHead, relative_features
from sdwan.model import MODEL_SCHEMA, Forecaster
from sdwan.optimization import prepare_partition, validate_dataset


def test_relative_features_preserve_input_and_convert_only_observed_lags():
    matrix = np.tile(np.arange(26), (3, 1)).astype(float)
    before = matrix.copy()
    transformed = relative_features(matrix)
    np.testing.assert_array_equal(matrix, before)
    np.testing.assert_array_equal(transformed[0, :6], [0, 1, 2, 3, 4, 150])
    np.testing.assert_array_equal(transformed[0, 6:12], [6, 1, 2, 3, 10, 330])


def head_spec():
    return {
        "schema": "relative_lags_v1",
        "center": [0] * 26,
        "scale": [1] * 26,
        "coefficients": [0] * 26,
        "intercept": 7,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(schema="other"),
        lambda s: s.update(center=[0]),
        lambda s: s["scale"].__setitem__(0, 0),
        lambda s: s["coefficients"].__setitem__(0, float("nan")),
        lambda s: s.update(intercept=float("inf")),
    ],
)
def test_corrupted_linear_head_never_loads(mutation):
    specification = head_spec()
    mutation(specification)
    with pytest.raises(ValueError, match="linear head"):
        LinearHead(specification)


def test_saved_linear_heads_reproduce_inference_without_xgboost_files(tmp_path):
    metadata = {
        "schema": MODEL_SCHEMA,
        "feature_names": FEATURE_NAMES,
        "horizon_steps": HORIZON,
        "enabled_metrics": dict.fromkeys(METRICS, True),
        "blend_weights": dict.fromkeys(METRICS, 0.5),
        "linear_heads": {metric: head_spec() for metric in METRICS},
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    matrix = np.zeros((2, 26))
    matrix[:, [0, 6, 12, 18]] = [20, 3, 0, 50]
    np.testing.assert_allclose(
        Forecaster(tmp_path).predict_matrix(matrix), [[23.5, 6.5, 3.5, 53.5]] * 2
    )


def test_selection_scaling_and_model_do_not_read_acceptance_or_holdout():
    first = recorded_document(count=6)
    second = copy.deepcopy(first)
    for episode in second["episodes"][3:]:
        for series in episode["series"].values():
            for sample in series:
                sample["latency_ms"] += 500
    selected = []
    for document in (first, second):
        dataset = validate_dataset(document)
        train = prepare_partition(dataset.episodes[:2], "strict")
        tune = prepare_partition(dataset.episodes[2:3], "strict")
        parameters, model, weight, mae, _ = select_candidate(train, tune, 0)
        selected.append((parameters, weight, mae, model.predict(tune.x)))
    assert selected[0][:3] == selected[1][:3]
    np.testing.assert_array_equal(selected[0][3], selected[1][3])


def test_candidate_bundle_roundtrip_and_existing_evidence_preserved(tmp_path):
    output = tmp_path / "candidate"
    report = train_search(
        validate_dataset(recorded_document(count=7)),
        output,
        counts=(2, 1, 3, 1),
        provenance="test_fixture",
    )
    assert report["runtime_promoted"] is False
    assert report["testbed_provenance"] == "test_fixture"
    loaded = Forecaster(output)
    assert loaded.metadata["partition_episodes"]["holdout"] == ["trial-6"]
    before = (output / "metadata.json").read_bytes()
    with pytest.raises(FileExistsError):
        train_search(validate_dataset(recorded_document(count=7)), output, counts=(2, 1, 3, 1))
    assert (output / "metadata.json").read_bytes() == before


def test_packet_suite_varies_seeds_and_has_no_model_or_future_features():
    plan = campaign.plan()
    assert len(set(plan["seeds"])) == sum(plan["partitions"].values()) == 12
    assert plan["flexiwan_in_path"] is False and plan["real_isp"] is False
    first = campaign.conditions(plan["seeds"][0], 0, 50)
    assert first == campaign.conditions(plan["seeds"][0], 0, 50)
    assert first != campaign.conditions(plan["seeds"][1], 0, 50)
    regimes = set()
    for seed in plan["seeds"]:
        for index in range(3):
            for elapsed in range(0, 125, 5):
                config = campaign.conditions(seed, index, elapsed)
                assert 0 < config["delay_ms"] < 110
                assert 0 <= config["loss_pct"] <= 1
                assert 0.1 <= config["load_fraction"] <= 0.8
                regimes.add(config["regime"])
    assert regimes == {"stable", "ramp", "step_busy", "recovery"}


def test_fresh_packet_plan_cannot_reuse_previous_seed_block():
    previous = campaign.plan()
    fresh = campaign.plan(seed_base=2026181700, counts=(0, 3, 3, 3))
    assert fresh['partitions'] == {'train': 0, 'tune': 3, 'accept': 3, 'holdout': 3}
    assert len(fresh['seeds']) == len(set(fresh['seeds'])) == 9
    assert set(previous['seeds']).isdisjoint(fresh['seeds'])
    assert campaign.plan() == previous


@pytest.mark.parametrize('counts', [(-1, 3, 3, 3), (0, 0, 0, 0), (1, 2, 3)])
def test_invalid_packet_plan_fails_before_collecting(counts):
    with pytest.raises(ValueError):
        campaign.plan(counts=counts)


def test_partial_namespace_failure_cleans_up_only_successful_creates(tmp_path, monkeypatch):
    calls = []

    def command(*args):
        calls.append(args)
        if args[:3] == ("ip", "netns", "add") and args[3].endswith("b"):
            raise RuntimeError("existing namespace")
        return ""

    monkeypatch.setattr(campaign, "command", command)
    monkeypatch.setattr(campaign.os, "geteuid", lambda: 0)
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    original_exists = campaign.Path.exists
    monkeypatch.setattr(
        campaign.Path,
        "exists",
        lambda path: (
            True if str(path) == "/opt/ai-sdwan-lab/.ai-sdwan-owned" else original_exists(path)
        ),
    )
    with pytest.raises(RuntimeError, match="existing namespace"):
        campaign.run(tmp_path / "run")
    deleted = [args[3] for args in calls if args[:3] == ("ip", "netns", "delete")]
    assert len(deleted) == 1 and deleted[0].endswith("a")
    status = json.loads((tmp_path / "run/status.json").read_text())
    assert status["cleanup_complete"] is True and status["completed"] is False

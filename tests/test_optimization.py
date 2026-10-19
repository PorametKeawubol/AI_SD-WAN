import copy
import json

import numpy as np
import pytest

from sdwan.domain import LINKS, METRICS, Telemetry
from sdwan.model import Forecaster, fingerprint
from sdwan.optimization import (
    acceptance_gate,
    chronological_split,
    main,
    prepare_partition,
    synthetic_dataset,
    train_candidate,
    tuning_selection,
    validate_dataset,
)


def recorded_document(count=6, samples=22):
    return {
        "schema": 1,
        "data_kind": "real_recorded_probe",
        "sampling": "strict",
        "episodes": [
            {
                "id": f"trial-{episode}",
                "series": {
                    link: [
                        Telemetry(
                            link_id=link,
                            timestamp=1000 + episode * 1000 + tick * 5,
                            up=True,
                            latency_ms=20 + tick,
                            jitter_ms=3,
                            loss_pct=0,
                            utilization_pct=30,
                        ).model_dump()
                        for tick in range(samples)
                    ]
                    for link in LINKS
                },
            }
            for episode in range(count)
        ],
    }


def test_episode_split_keeps_all_wans_together_and_never_crosses_trials():
    dataset = validate_dataset(recorded_document())
    splits = chronological_split(dataset, 2, 1, 2, 1)
    assert [[episode.id for episode in episodes] for episodes in splits.values()] == [
        ["trial-0", "trial-1"],
        ["trial-2"],
        ["trial-3", "trial-4"],
        ["trial-5"],
    ]
    partition = prepare_partition(splits["train"], "strict")
    assert partition.x.shape == (30, 26)  # Five eligible windows per WAN per trial.
    assert set(partition.groups) == {"trial-0", "trial-1"}
    assert partition.y[:, 0].min() == 37
    # Eleven frames in one episode never borrow a history or target from the next.
    short = validate_dataset(recorded_document(samples=11))
    assert prepare_partition(short.episodes, "strict").x.shape == (0, 26)
    with pytest.raises(ValueError, match="exact episode"):
        chronological_split(dataset, 2, 1, 1, 1)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda document: document["episodes"].reverse(), "chronological"),
        (lambda document: document["episodes"][1].update(id="trial-0"), "unique"),
        (lambda document: document["episodes"][0]["series"].pop("wan3"), "three WAN"),
        (
            lambda document: document["episodes"][0]["series"]["wan1"][1].update(timestamp=1000),
            "strictly increasing",
        ),
        (
            lambda document: document["episodes"][0]["series"]["wan1"][0].update(link_id="wan2"),
            "identity",
        ),
    ],
)
def test_dataset_refuses_ambiguous_groups_or_timestamps(mutation, message):
    document = recorded_document()
    mutation(document)
    with pytest.raises(ValueError, match=message):
        validate_dataset(document)


def test_training_masks_unavailable_down_targets_and_rejects_down_histories():
    document = recorded_document(count=1, samples=18)
    for rows in document["episodes"][0]["series"].values():
        rows[17].update(up=False, latency_ms=10000, jitter_ms=5000, loss_pct=100)
    partition = prepare_partition(validate_dataset(document).episodes, "strict")
    assert partition.x.shape == (3, 26)
    assert partition.available.tolist() == [[False, False, True, True]] * 3
    assert np.all(partition.y[:, 2] == 100)
    assert np.all(partition.x[:, 24] == 1)
    assert partition.counters["future_down_windows"] == 3
    for rows in document["episodes"][0]["series"].values():
        rows[5].update(up=False)
    rejected = prepare_partition(validate_dataset(document).episodes, "strict")
    assert len(rejected.x) == 0
    assert rejected.counters["history_rejected"] > 0


def test_future_values_and_load_commands_never_enter_current_features():
    document = recorded_document(count=1)
    original = prepare_partition(validate_dataset(document).episodes, "strict")
    changed = copy.deepcopy(document)
    changed["phases"] = [{"future_commanded_load_mbps": 999999}]
    for rows in changed["episodes"][0]["series"].values():
        rows[17]["latency_ms"] = 900
    updated = prepare_partition(validate_dataset(changed).episodes, "strict")
    assert np.array_equal(original.x[0], updated.x[0])
    assert original.y[0, 0] != updated.y[0, 0]


def test_tuning_selection_has_no_acceptance_input_and_ties_prefer_persistence():
    target = np.ones(40) * 10
    current = np.zeros(40)
    model3, model5 = object(), object()
    chosen = tuning_selection(
        target, current, [(3, model3, np.ones(40) * 20), (5, model5, np.ones(40) * 30)], 0
    )
    assert chosen["weight"] == 0.5 and chosen["depth"] == 3
    # Deliberately contradictory acceptance rejects rather than selecting model5/alpha0.25.
    gate = acceptance_gate(
        np.zeros(40),
        np.ones(40) * 10,
        current,
        np.repeat(["a", "b", "c", "d"], 10),
        weight=chosen["weight"],
    )
    assert not gate["accepted"]
    assert chosen["weight"] == 0.5 and chosen["depth"] == 3
    tied = tuning_selection(current, current, [(5, model5, current), (3, model3, current)], 0)
    assert tied["weight"] == 0 and tied["depth"] == 3


def test_acceptance_requires_episode_evidence_not_many_correlated_rows():
    target, baseline, prediction = np.ones(40) * 10, np.zeros(40), np.ones(40) * 8
    groups = np.repeat(["a", "b", "c", "d"], 10)
    passed = acceptance_gate(target, prediction, baseline, groups, weight=0.5)
    assert passed["accepted"]
    assert passed["relative_mae_improvement"] == pytest.approx(0.8)
    assert passed["bootstrap_95_ci_episode_blocks"] == pytest.approx([-8, -8])
    correlated = acceptance_gate(target, prediction, baseline, np.repeat("a", 40), weight=0.5)
    assert not correlated["accepted"]
    assert "insufficient_acceptance_episode_groups" in correlated["reasons"]
    sparse = acceptance_gate(target[:12], prediction[:12], baseline[:12], groups[:12], weight=0.5)
    assert "insufficient_acceptance_samples" in sparse["reasons"]
    fallback = acceptance_gate(target, baseline, baseline, groups, weight=0)
    assert not fallback["accepted"] and "tuning_selected_persistence" in fallback["reasons"]


def test_acceptance_block_ci_can_reject_positive_row_weighted_improvement():
    # A large favorable trial cannot make three adverse trials independent evidence.
    groups = np.asarray(["large"] * 100 + ["b"] * 10 + ["c"] * 10 + ["d"] * 10)
    target = np.ones(len(groups)) * 10
    baseline = np.zeros(len(groups))
    prediction = np.asarray([10] * 100 + [-1] * 30)
    gate = acceptance_gate(target, prediction, baseline, groups, weight=1)
    assert gate["relative_mae_improvement"] > 0.02
    assert gate["bootstrap_95_ci_episode_blocks"][1] >= 0
    assert not gate["accepted"]


def test_full_small_candidate_roundtrip_without_runtime_promotion(tmp_path):
    dataset = synthetic_dataset(6, seed_offset=250000)
    output = tmp_path / "candidate"
    report = train_candidate(
        dataset,
        output,
        train_count=2,
        tune_count=1,
        accept_count=3,
        holdout_count=0,
        bootstrap_resamples=200,
    )
    model = Forecaster(output)
    result = model.predict_matrix(prepare_partition(dataset.episodes[-1:], "strict").x)
    assert result.shape[1] == 4 and np.all(np.isfinite(result))
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["offline_candidate"] and not metadata["runtime_promoted"]
    assert metadata["data_kind"] == "synthetic"
    assert report["model_sha256"] == fingerprint(output)
    assert (output / "report" / "optimization.json").is_file()
    groups = metadata["partition_episodes"]
    assert len(set(groups["train"] + groups["tune"] + groups["accept"])) == 6
    for metric in METRICS:
        stats = report["metrics"][metric]
        assert stats["partitions"]["holdout"]["samples"] == 0
        assert stats["partitions"]["holdout"]["selected_mae"] is None
        if not stats["acceptance_gate"]["accepted"]:
            assert metadata["blend_weights"][metric] == 0
    before = fingerprint(output)
    with pytest.raises(FileExistsError, match="Existing output preserved"):
        train_candidate(
            dataset, output, train_count=2, tune_count=1, accept_count=3, holdout_count=0
        )
    assert fingerprint(output) == before


def test_existing_empty_output_is_also_preserved(tmp_path):
    output = tmp_path / "candidate"
    output.mkdir()
    with pytest.raises(FileExistsError):
        train_candidate(
            validate_dataset(recorded_document()),
            output,
            train_count=2,
            tune_count=1,
            accept_count=3,
            holdout_count=0,
        )
    assert list(output.iterdir()) == []


def test_plan_does_not_train_write_or_promote(capsys, tmp_path):
    output = tmp_path / "unused"
    main(["--plan", "--output", str(output)])
    plan = json.loads(capsys.readouterr().out)
    assert plan["partitions"] == {"train": 60, "tune": 15, "accept": 30, "holdout": 10}
    assert plan["runtime_promotion"] is False and plan["network_access"] is False
    assert not output.exists()


def test_fit_failure_never_publishes_or_leaves_a_partial_bundle(tmp_path, monkeypatch):
    from sdwan import optimization

    class BrokenModel:
        def __init__(self, **kwargs):
            pass

        def fit(self, *args, **kwargs):
            raise RuntimeError("Fixture training failure")

    monkeypatch.setattr(optimization, "XGBRegressor", BrokenModel)
    output = tmp_path / "candidate"
    with pytest.raises(RuntimeError, match="Fixture training failure"):
        train_candidate(
            validate_dataset(recorded_document()),
            output,
            train_count=2,
            tune_count=1,
            accept_count=3,
            holdout_count=0,
        )
    assert not output.exists() and not list(tmp_path.glob(".candidate-*"))


def test_acceptance_and_holdout_cannot_change_the_selected_parameters(tmp_path):
    first = recorded_document(count=6)
    second = copy.deepcopy(first)
    # These labels belong to groups which selection must never inspect.
    for entry in second["episodes"][3:]:
        for series in entry["series"].values():
            for row in series:
                row["latency_ms"] += 500
                row["jitter_ms"] += 100
    outputs = []
    for index, document in enumerate((first, second)):
        path = tmp_path / f"candidate-{index}"
        train_candidate(
            validate_dataset(document),
            path,
            train_count=2,
            tune_count=1,
            accept_count=2,
            holdout_count=1,
            bootstrap_resamples=200,
        )
        outputs.append(json.loads((path / "metadata.json").read_text())["selected_parameters"])
    for metric in METRICS:
        for key in ("max_depth", "best_iteration", "tuning_blend_weight"):
            assert outputs[0][metric][key] == outputs[1][metric][key]

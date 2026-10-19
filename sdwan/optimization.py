"""Bounded offline forecasting experiments; this module never deploys a model."""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xgboost as xgb
from xgboost import XGBRegressor

from lab.evaluate_forecast import evaluation_windows

from .domain import HORIZON, INTERVAL, LINKS, METRICS, WINDOW, Telemetry
from .features import FEATURE_NAMES
from .model import MODEL_SCHEMA, Forecaster, fingerprint
from .simulator import training_episode

DEPTHS = (3, 5)
BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
MIN_ACCEPT_EPISODES = 3
MIN_ACCEPT_SAMPLES = 30
MIN_RELATIVE_IMPROVEMENT = 0.02


@dataclass
class Episode:
    id: str
    series: dict[str, list[Telemetry]]


@dataclass
class Dataset:
    data_kind: str
    sampling: str
    episodes: list[Episode]
    sha256: str


@dataclass
class Partition:
    x: np.ndarray
    y: np.ndarray
    available: np.ndarray
    groups: np.ndarray
    counters: dict


def validate_dataset(document):
    """Validate whole chronological episodes before constructing any windows."""
    if not isinstance(document, dict):
        raise TypeError("Expected a dataset object")
    if document.get("schema") != 1 or isinstance(document.get("schema"), bool):
        raise ValueError("Expected dataset schema 1")
    if document.get("data_kind") not in {"real_recorded_probe", "synthetic"}:
        raise ValueError("Unknown dataset data_kind")
    if document.get("sampling") not in {"strict", "causal_linear_5s"}:
        raise ValueError("Unknown dataset sampling contract")
    entries = document.get("episodes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Dataset requires complete episodes")
    episodes, identifiers = [], set()
    previous_end = float("-inf")
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("Expected episode objects")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("Episode IDs must be nonempty and unique")
        series = entry.get("series")
        if not isinstance(series, dict) or set(series) != set(LINKS):
            raise ValueError("Each episode must contain exactly the three WAN series")
        parsed = {}
        for link, rows in series.items():
            if not isinstance(rows, list) or not rows:
                raise ValueError("Each WAN requires measured samples")
            samples = [Telemetry.model_validate(row) for row in rows]
            if any(sample.link_id != link for sample in samples):
                raise ValueError("WAN series and sample identity differ")
            timestamps = [sample.timestamp for sample in samples]
            if timestamps != sorted(set(timestamps)):
                raise ValueError("WAN timestamps must be strictly increasing")
            parsed[link] = samples
        start = min(samples[0].timestamp for samples in parsed.values())
        end = max(samples[-1].timestamp for samples in parsed.values())
        if start <= previous_end:
            raise ValueError("Episodes must be chronological and have nonoverlapping time ranges")
        previous_end = end
        identifiers.add(identifier)
        episodes.append(Episode(identifier, parsed))
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return Dataset(
        document["data_kind"],
        document["sampling"],
        episodes,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


def synthetic_dataset(count, seed_offset=200000):
    if count < 1 or seed_offset < 10000:
        raise ValueError("Synthetic episodes require an independent seed block >=10000")
    return validate_dataset(
        {
            "schema": 1,
            "data_kind": "synthetic",
            "sampling": "strict",
            "generator": "sdwan.simulator.training_episode",
            "seed_offset": seed_offset,
            "episodes": [
                {
                    "id": f"synthetic-{seed_offset + index}",
                    "series": {
                        link: [sample.model_dump() for sample in samples]
                        for link, samples in training_episode(seed_offset + index, index).items()
                    },
                }
                for index in range(count)
            ],
        }
    )


def chronological_split(dataset, train_count, tune_count, accept_count, holdout_count=0):
    counts = (train_count, tune_count, accept_count, holdout_count)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
        raise ValueError("Episode counts must be integers")
    if min(counts[:3]) < 1 or holdout_count < 0 or sum(counts) != len(dataset.episodes):
        raise ValueError("Positive train/tune/accept counts and exact episode coverage required")
    result, cursor = {}, 0
    for name, count in zip(("train", "tune", "accept", "holdout"), counts):
        result[name] = dataset.episodes[cursor : cursor + count]
        cursor += count
    return result


def prepare_partition(episodes, sampling):
    """No history or t+30s label can cross an episode or WAN boundary."""
    inputs, targets, masks, groups = [], [], [], []
    counters = Counter()
    for episode in episodes:
        for series in episode.series.values():
            windows, counts = evaluation_windows(series, sampling)
            counters.update(counts)
            for window in windows:
                inputs.append(window["matrix"])
                targets.append(window["target"])
                masks.append(window["available_metrics"])
                groups.append(episode.id)
    return Partition(
        np.asarray(inputs, dtype=np.float32).reshape(-1, len(FEATURE_NAMES)),
        np.asarray(targets, dtype=np.float32).reshape(-1, len(METRICS)),
        np.asarray(masks, dtype=bool).reshape(-1, len(METRICS)),
        np.asarray(groups, dtype=str),
        dict(counters),
    )


def clip_predictions(values, metric_index):
    return np.clip(values, 0, 100 if metric_index >= 2 else None)


def tuning_selection(target, current, candidates, metric_index):
    """Only tuning labels are accepted; acceptance/holdout are not parameters."""
    choices = []
    for depth, model, delta in candidates:
        for weight in BLEND_WEIGHTS:
            prediction = clip_predictions(current + weight * delta, metric_index)
            mae = float(np.abs(prediction - target).mean())
            choices.append((mae, weight, depth, model))
    # Equal performance favors persistence, then the shallower model.
    mae, weight, depth, model = min(choices, key=lambda row: row[:3])
    return {"model": model, "depth": depth, "weight": weight, "mae": mae}


def acceptance_gate(
    target,
    prediction,
    persistence,
    groups,
    *,
    weight,
    bootstrap_resamples=2000,
    bootstrap_seed=20261011,
):
    """An untouched episode-block gate can reject, never choose a new candidate."""
    if not 100 <= bootstrap_resamples <= 10000:
        raise ValueError("Bootstrap resamples must be between 100 and 10000")
    target, prediction, persistence = map(np.asarray, (target, prediction, persistence))
    groups = np.asarray(groups)
    if not (target.ndim == prediction.ndim == persistence.ndim == groups.ndim == 1):
        raise ValueError("Acceptance vectors must be one-dimensional")
    if not (len(target) == len(prediction) == len(persistence) == len(groups)):
        raise ValueError("Acceptance vector lengths differ")
    if not all(np.all(np.isfinite(values)) for values in (target, prediction, persistence)):
        raise ValueError("Acceptance values must be finite")
    errors = np.abs(prediction - target)
    base_errors = np.abs(persistence - target)
    identifiers = sorted(set(groups.tolist()))
    differences = np.asarray(
        [
            float((errors[groups == identifier] - base_errors[groups == identifier]).mean())
            for identifier in identifiers
        ]
    )
    mae = float(errors.mean()) if len(target) else None
    baseline_mae = float(base_errors.mean()) if len(target) else None
    improvement = (baseline_mae - mae) / baseline_mae if baseline_mae else None
    interval = None
    if len(differences) >= MIN_ACCEPT_EPISODES:
        rng = np.random.default_rng(bootstrap_seed)
        means = rng.choice(differences, size=(bootstrap_resamples, len(differences))).mean(axis=1)
        interval = [float(value) for value in np.percentile(means, [2.5, 97.5])]
    reasons = []
    if weight == 0:
        reasons.append("tuning_selected_persistence")
    if len(target) < MIN_ACCEPT_SAMPLES:
        reasons.append("insufficient_acceptance_samples")
    if len(differences) < MIN_ACCEPT_EPISODES:
        reasons.append("insufficient_acceptance_episode_groups")
    if improvement is None or improvement < MIN_RELATIVE_IMPROVEMENT:
        reasons.append("relative_mae_improvement_below_2pct")
    if interval is None or interval[1] >= 0:
        reasons.append("episode_block_ci_does_not_exclude_zero")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "samples": len(target),
        "episode_groups": len(identifiers),
        "selected_mae": mae,
        "persistence_mae": baseline_mae,
        "relative_mae_improvement": improvement,
        "paired_episode_mean_mae_difference": float(differences.mean())
        if len(differences)
        else None,
        "bootstrap_95_ci_episode_blocks": interval,
    }


def partition_scores(partition, metric_index, model, selected_weight, deployed_weight):
    available = partition.available[:, metric_index]
    matrix = partition.x[available]
    current = matrix[:, metric_index * 6]
    target = partition.y[available, metric_index]
    delta = model.predict(matrix) if len(matrix) else np.asarray([])
    return {
        "samples": len(target),
        "episode_groups": len(set(partition.groups[available].tolist())),
        "persistence_mae": float(np.abs(current - target).mean()) if len(target) else None,
        "selected_mae": float(
            np.abs(
                clip_predictions(current + selected_weight * delta, metric_index) - target
            ).mean()
        )
        if len(target)
        else None,
        "deployed_mae": float(
            np.abs(
                clip_predictions(current + deployed_weight * delta, metric_index) - target
            ).mean()
        )
        if len(target)
        else None,
    }


def optimization_plan(train_count=60, tune_count=15, accept_count=30, holdout_count=10):
    return {
        "partitions": dict(
            zip(
                ("train", "tune", "accept", "holdout"),
                (train_count, tune_count, accept_count, holdout_count),
            )
        ),
        "grouping": "whole chronological episodes; all WANs kept together",
        "max_depths": list(DEPTHS),
        "blend_weights": list(BLEND_WEIGHTS),
        "selection": "tuning only; ties favor smaller blend weight then shallower trees",
        "acceptance": {
            "minimum_relative_mae_improvement": MIN_RELATIVE_IMPROVEMENT,
            "minimum_episode_groups": MIN_ACCEPT_EPISODES,
            "minimum_samples_per_metric": MIN_ACCEPT_SAMPLES,
            "paired_episode_block_95_ci_upper_bound": "strictly below zero",
            "rejection": "persistence; never retry a different candidate on acceptance",
        },
        "holdout": "diagnostic only, evaluated after selection and acceptance are frozen",
        "runtime_promotion": False,
        "network_access": False,
    }


def train_candidate(
    dataset,
    output,
    *,
    train_count=60,
    tune_count=15,
    accept_count=30,
    holdout_count=10,
    bootstrap_resamples=2000,
    bootstrap_seed=20261011,
    baseline_model=None,
):
    """Publish a fresh, complete offline bundle; existing destinations are preserved."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Existing output preserved; choose a fresh candidate directory")
    splits = chronological_split(dataset, train_count, tune_count, accept_count, holdout_count)
    partitions = {
        name: prepare_partition(episodes, dataset.sampling) for name, episodes in splits.items()
    }
    for name in ("train", "tune"):
        if any(not np.any(partitions[name].available[:, index]) for index in range(len(METRICS))):
            raise ValueError(f"{name} requires eligible targets for every metric")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        metrics, selected_parameters, enabled, weights = {}, {}, {}, {}
        for index, metric in enumerate(METRICS):
            training, tuning, acceptance = (
                partitions[name] for name in ("train", "tune", "accept")
            )
            train_mask, tune_mask = training.available[:, index], tuning.available[:, index]
            x_train, x_tune = training.x[train_mask], tuning.x[tune_mask]
            current_tune = x_tune[:, index * 6]
            candidates = []
            for depth in DEPTHS:
                model = XGBRegressor(
                    n_estimators=240,
                    max_depth=depth,
                    learning_rate=0.05,
                    subsample=0.85,
                    colsample_bytree=0.9,
                    reg_lambda=5,
                    objective="reg:absoluteerror",
                    eval_metric="mae",
                    tree_method="hist",
                    n_jobs=2,
                    random_state=42,
                    early_stopping_rounds=20,
                )
                model.fit(
                    x_train,
                    training.y[train_mask, index] - x_train[:, index * 6],
                    eval_set=[(x_tune, tuning.y[tune_mask, index] - current_tune)],
                    verbose=False,
                )
                candidates.append((depth, model, model.predict(x_tune)))
            chosen = tuning_selection(tuning.y[tune_mask, index], current_tune, candidates, index)
            model, selected_weight = chosen["model"], chosen["weight"]
            accept_mask = acceptance.available[:, index]
            x_accept = acceptance.x[accept_mask]
            current_accept = x_accept[:, index * 6]
            delta = model.predict(x_accept) if len(x_accept) else np.asarray([])
            gate = acceptance_gate(
                acceptance.y[accept_mask, index],
                clip_predictions(current_accept + selected_weight * delta, index),
                current_accept,
                acceptance.groups[accept_mask],
                weight=selected_weight,
                bootstrap_resamples=bootstrap_resamples,
                bootstrap_seed=bootstrap_seed + index,
            )
            deployed_weight = selected_weight if gate["accepted"] else 0.0
            model.save_model(staging / f"{metric}.json")
            enabled[metric], weights[metric] = deployed_weight > 0, deployed_weight
            selected_parameters[metric] = {
                "max_depth": chosen["depth"],
                "best_iteration": model.best_iteration,
                "tuning_blend_weight": selected_weight,
                "deployed_blend_weight": deployed_weight,
            }
            metrics[metric] = {
                "acceptance_gate": gate,
                "partitions": {
                    name: partition_scores(
                        partition, index, model, selected_weight, deployed_weight
                    )
                    for name, partition in partitions.items()
                },
            }
        protocol = optimization_plan(train_count, tune_count, accept_count, holdout_count)
        protocol.update(
            {"bootstrap_resamples": bootstrap_resamples, "bootstrap_seed": bootstrap_seed}
        )
        metadata = {
            "schema": MODEL_SCHEMA,
            "feature_names": FEATURE_NAMES,
            "horizon_steps": HORIZON,
            "horizon_seconds": HORIZON * INTERVAL,
            "window_steps": WINDOW,
            "interval_seconds": INTERVAL,
            "data_kind": dataset.data_kind,
            "forecast_sampling": dataset.sampling,
            "dataset_sha256": dataset.sha256,
            "xgboost_version": xgb.__version__,
            "training_rows": len(partitions["train"].x),
            "validation_rows": len(partitions["tune"].x),
            "partition_episodes": {
                name: [episode.id for episode in episodes] for name, episodes in splits.items()
            },
            "selection": protocol["selection"],
            "split": protocol["grouping"],
            "label": "actual eligible metric at t+30 seconds; unavailable targets masked per metric",
            "target_mode": "future metric minus current metric; MAE objective",
            "deployment_gate": protocol["acceptance"],
            "enabled_metrics": enabled,
            "blend_weights": weights,
            "selected_parameters": selected_parameters,
            "metrics": metrics,
            "offline_candidate": True,
            "runtime_promoted": False,
        }
        (staging / "metadata.json").write_text(
            json.dumps(metadata, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "data_kind": dataset.data_kind,
            "dataset_sha256": dataset.sha256,
            "model_sha256": fingerprint(staging),
            "protocol": protocol,
            "partition_episodes": metadata["partition_episodes"],
            "window_counters": {name: partition.counters for name, partition in partitions.items()},
            "metrics": metrics,
            "blend_weights": weights,
            "runtime_promoted": False,
            "model_validated_on_real_data": False,
            "limitations": [
                "Episode groups, not overlapping windows, are the bootstrap resampling unit.",
                "Acceptance gates forecast MAE; routing and application benefits require separate paired trials.",
                "Rejected metrics use persistence; no alternative is selected after seeing acceptance.",
                "Holdout results are diagnostic and never change selected parameters or weights.",
                "No live controller, model deployment, telemetry mutation, or network access is performed.",
            ],
        }
        # Baseline is evaluated only after the candidate's selection and gate are frozen.
        if baseline_model is not None:
            baseline = Forecaster(baseline_model)
            comparison = {"model_sha256": fingerprint(baseline_model), "partitions": {}}
            for name, partition in partitions.items():
                prediction = baseline.predict_matrix(partition.x) if len(partition.x) else None
                comparison["partitions"][name] = {}
                for index, metric in enumerate(METRICS):
                    mask = partition.available[:, index]
                    comparison["partitions"][name][metric] = {
                        "samples": int(mask.sum()),
                        "mae": float(
                            np.abs(prediction[mask, index] - partition.y[mask, index]).mean()
                        )
                        if np.any(mask)
                        else None,
                    }
            report["frozen_baseline_diagnostic"] = comparison
        (staging / "report").mkdir()
        (staging / "report" / "optimization.json").write_text(
            json.dumps(report, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        # Loading all four files before publication detects incomplete/invalid bundles.
        Forecaster(staging)
        if output.exists() or output.is_symlink():
            raise FileExistsError("Output appeared during training; existing directory preserved")
        os.rename(staging, output)
        return report
    except BaseException:
        # Only the unique directory allocated by this invocation can be removed.
        if staging.exists() and staging.resolve().parent == output.parent.resolve():
            shutil.rmtree(staging)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--train-episodes", type=int, default=60)
    parser.add_argument("--tune-episodes", type=int, default=15)
    parser.add_argument("--accept-episodes", type=int, default=30)
    parser.add_argument("--holdout-episodes", type=int, default=10)
    parser.add_argument("--seed-offset", type=int, default=200000)
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20261011)
    parser.add_argument("--baseline-model", type=Path)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    counts = (args.train_episodes, args.tune_episodes, args.accept_episodes, args.holdout_episodes)
    if min(counts[:3]) < 1 or counts[3] < 0:
        parser.error("train/tune/accept must be positive; holdout may be zero")
    if args.plan:
        print(json.dumps(optimization_plan(*counts), indent=2))
        return
    if args.output is None:
        parser.error("--output requires a fresh candidate directory")
    dataset = (
        validate_dataset(json.loads(args.dataset.read_text(encoding="utf-8")))
        if args.dataset
        else synthetic_dataset(sum(counts), args.seed_offset)
    )
    report = train_candidate(
        dataset,
        args.output,
        train_count=counts[0],
        tune_count=counts[1],
        accept_count=counts[2],
        holdout_count=counts[3],
        bootstrap_resamples=args.bootstrap_resamples,
        bootstrap_seed=args.bootstrap_seed,
        baseline_model=args.baseline_model,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "model_sha256": report["model_sha256"],
                "blend_weights": report["blend_weights"],
                "runtime_promoted": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

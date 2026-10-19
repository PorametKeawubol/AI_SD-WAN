"""Predeclared tree/linear residual search; no live deployment or network access."""

import argparse
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from xgboost import XGBRegressor

from .domain import HORIZON, INTERVAL, METRICS, WINDOW
from .features import FEATURE_NAMES
from .linear import LinearHead, relative_features
from .model import MODEL_SCHEMA, Forecaster, fingerprint
from .optimization import (
    BLEND_WEIGHTS,
    acceptance_gate,
    chronological_split,
    clip_predictions,
    partition_scores,
    prepare_partition,
    validate_dataset,
)

RIDGE_ALPHAS = (0.1, 1.0, 10.0, 100.0, 1000.0)
TREE_CONFIGS = ((2, 5, 1), (3, 5, 1), (5, 5, 1), (2, 20, 10))


def search_plan():
    return {
        "schema": 1,
        "linear_alphas": list(RIDGE_ALPHAS),
        "linear_features": "current level plus relative lag1/lag3/mean, stddev, observed slope*30; training-only standardization",
        "trees_depth_lambda_min_child": list(TREE_CONFIGS),
        "tree_parameters": {
            "trees": 240,
            "learning_rate": 0.05,
            "objective": "reg:absoluteerror",
            "early_stopping": 20,
        },
        "blend_weights": list(BLEND_WEIGHTS),
        "selection": "minimum tuning MAE only; ties prefer smaller weight then linear before trees in declared order",
        "acceptance": "untouched chronological acceptance; >=2% MAE reduction, >=3 episode groups, >=30 samples, paired episode-block bootstrap 95% CI upper<0",
        "rejection": "use persistence; do not try another candidate on acceptance",
        "holdout": "report only after selection and acceptance are frozen; no retuning",
        "runtime_promoted": False,
    }


def fit_candidates(training, tuning, index):
    mask, tune_mask = training.available[:, index], tuning.available[:, index]
    x_train, x_tune = training.x[mask], tuning.x[tune_mask]
    y_train = training.y[mask, index] - x_train[:, index * 6]
    y_tune = tuning.y[tune_mask, index] - x_tune[:, index * 6]
    transformed = relative_features(x_train)
    center, scale = transformed.mean(axis=0), transformed.std(axis=0)
    scale[scale < 1e-8] = 1
    candidates = []
    for alpha in RIDGE_ALPHAS:
        linear = Ridge(alpha=alpha).fit((transformed - center) / scale, y_train)
        spec = {
            "schema": "relative_lags_v1",
            "center": center.tolist(),
            "scale": scale.tolist(),
            "coefficients": linear.coef_.tolist(),
            "intercept": float(linear.intercept_),
        }
        candidates.append(({"family": "ridge", "alpha": alpha}, LinearHead(spec)))
    for depth, regularization, child_weight in TREE_CONFIGS:
        model = XGBRegressor(
            n_estimators=240,
            max_depth=depth,
            reg_lambda=regularization,
            min_child_weight=child_weight,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.9,
            objective="reg:absoluteerror",
            eval_metric="mae",
            tree_method="hist",
            n_jobs=2,
            random_state=42,
            early_stopping_rounds=20,
        )
        model.fit(x_train, y_train, eval_set=[(x_tune, y_tune)], verbose=False)
        candidates.append(
            (
                {
                    "family": "xgboost",
                    "depth": depth,
                    "lambda": regularization,
                    "min_child_weight": child_weight,
                    "best_iteration": model.best_iteration,
                },
                model,
            )
        )
    return candidates


def select_candidate(training, tuning, index):
    mask = tuning.available[:, index]
    matrix, target = tuning.x[mask], tuning.y[mask, index]
    current = matrix[:, index * 6]
    choices, audit = [], []
    for order, (parameters, model) in enumerate(fit_candidates(training, tuning, index)):
        delta = model.predict(matrix)
        for weight in BLEND_WEIGHTS:
            mae = float(np.abs(clip_predictions(current + weight * delta, index) - target).mean())
            audit.append({**parameters, "weight": weight, "tuning_mae": mae})
            choices.append((mae, weight, order, parameters, model))
    mae, weight, _, parameters, model = min(choices, key=lambda value: value[:3])
    return parameters, model, weight, mae, audit


def train_search(
    dataset, output, counts=(4, 2, 3, 3), baseline_model=None, *, provenance="exploratory"
):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Existing candidate preserved")
    splits = chronological_split(dataset, *counts)
    partitions = {
        name: prepare_partition(episodes, dataset.sampling) for name, episodes in splits.items()
    }
    for name in ("train", "tune"):
        if any(not np.any(partitions[name].available[:, i]) for i in range(4)):
            raise ValueError(f"{name} has no eligible targets")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        weights, heads, selected, metrics, tuning_audit = {}, {}, {}, {}, {}
        for index, metric in enumerate(METRICS):
            parameters, model, weight, tuning_mae, audit = select_candidate(
                partitions["train"], partitions["tune"], index
            )
            acceptance = partitions["accept"]
            mask = acceptance.available[:, index]
            matrix, target = acceptance.x[mask], acceptance.y[mask, index]
            current = matrix[:, index * 6]
            delta = model.predict(matrix) if len(matrix) else np.asarray([])
            gate = acceptance_gate(
                target,
                clip_predictions(current + weight * delta, index),
                current,
                acceptance.groups[mask],
                weight=weight,
                bootstrap_seed=20261017 + index,
            )
            deployed = weight if gate["accepted"] else 0.0
            weights[metric] = deployed
            selected[metric] = {
                **parameters,
                "tuning_weight": weight,
                "candidate_weight": deployed,
                "tuning_mae": tuning_mae,
            }
            tuning_audit[metric] = audit
            if isinstance(model, LinearHead):
                heads[metric] = model.specification
            else:
                model.save_model(staging / f"{metric}.json")
            metrics[metric] = {
                "acceptance_gate": gate,
                "partitions": {
                    name: partition_scores(partition, index, model, weight, deployed)
                    for name, partition in partitions.items()
                },
            }
        metadata = {
            "schema": MODEL_SCHEMA,
            "feature_names": FEATURE_NAMES,
            "horizon_steps": HORIZON,
            "horizon_seconds": HORIZON * INTERVAL,
            "window_steps": WINDOW,
            "interval_seconds": INTERVAL,
            "enabled_metrics": {metric: weights[metric] > 0 for metric in METRICS},
            "blend_weights": weights,
            "linear_heads": heads,
            "selected_parameters": selected,
            "data_kind": dataset.data_kind,
            "forecast_sampling": dataset.sampling,
            "dataset_sha256": dataset.sha256,
            "partition_episodes": {
                name: [episode.id for episode in episodes] for name, episodes in splits.items()
            },
            "offline_candidate": True,
            "runtime_promoted": False,
            "testbed_provenance": provenance,
        }
        (staging / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False))
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "protocol": search_plan(),
            **metadata,
            "metrics": metrics,
            "tuning_candidates": tuning_audit,
            "window_counters": {name: partition.counters for name, partition in partitions.items()},
        }
        if baseline_model:
            frozen = Forecaster(baseline_model)
            report["frozen_baseline"] = {"sha256": fingerprint(baseline_model), "partitions": {}}
            for name, partition in partitions.items():
                predictions = frozen.predict_matrix(partition.x)
                report["frozen_baseline"]["partitions"][name] = {
                    metric: float(
                        np.abs(
                            predictions[partition.available[:, i], i]
                            - partition.y[partition.available[:, i], i]
                        ).mean()
                    )
                    if np.any(partition.available[:, i])
                    else None
                    for i, metric in enumerate(METRICS)
                }
        candidate = Forecaster(staging)
        for index, metric in enumerate(METRICS):
            partition = partitions["holdout"]
            mask = partition.available[:, index]
            if np.any(mask):
                actual = float(
                    np.abs(
                        candidate.predict_matrix(partition.x)[mask, index]
                        - partition.y[mask, index]
                    ).mean()
                )
                if not np.isclose(
                    actual, metrics[metric]["partitions"]["holdout"]["deployed_mae"], atol=1e-5
                ):
                    raise RuntimeError("Saved candidate does not reproduce reported predictions")
        report["model_sha256"] = fingerprint(staging)
        (staging / "report").mkdir()
        (staging / "report" / "search.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
        if output.exists() or output.is_symlink():
            raise FileExistsError("Existing candidate preserved")
        os.rename(staging, output)
        return report
    except BaseException:
        if staging.exists() and staging.resolve().parent == output.parent.resolve():
            shutil.rmtree(staging)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--counts", nargs=4, type=int, default=(4, 2, 3, 3))
    parser.add_argument("--baseline-model", type=Path)
    parser.add_argument("--provenance", default="exploratory")
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(search_plan(), indent=2))
        return
    if not args.dataset or not args.output:
        parser.error("--dataset and --output required")
    report = train_search(
        validate_dataset(json.loads(args.dataset.read_text())),
        args.output,
        tuple(args.counts),
        args.baseline_model,
        provenance=args.provenance,
    )
    print(
        json.dumps(
            {
                "model_sha256": report["model_sha256"],
                "weights": report["blend_weights"],
                "metrics": report["metrics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

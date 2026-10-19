"""Reproduce saved forecast traces and exact tree contributions without retraining."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from xgboost import DMatrix

from lab.evaluate_forecast import evaluation_windows
from sdwan.domain import METRICS
from sdwan.features import FEATURE_NAMES
from sdwan.model import Forecaster, fingerprint
from sdwan.optimization import chronological_split, prepare_partition, validate_dataset


def explain(dataset_path, model_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a fresh output directory")
    dataset = validate_dataset(json.loads(Path(dataset_path).read_text()))
    forecaster = Forecaster(model_path)
    if dataset.sha256 != forecaster.metadata["dataset_sha256"]:
        raise ValueError("Dataset does not match the saved candidate")
    holdout_ids = forecaster.metadata["partition_episodes"]["holdout"]
    episode_counts = tuple(
        len(forecaster.metadata["partition_episodes"][p])
        for p in ("train", "tune", "accept", "holdout")
    )
    partitions = chronological_split(dataset, *episode_counts)
    if [e.id for e in partitions["holdout"]] != holdout_ids:
        raise ValueError("Holdout order mismatch")
    holdout = prepare_partition(partitions["holdout"], dataset.sampling)
    report = {
        "model_sha256": fingerprint(model_path),
        "dataset_sha256": dataset.sha256,
        "holdout_episodes": holdout_ids,
        "runtime_promoted": False,
        "contribution_scope": "Mean absolute exact TreeSHAP contribution to the learned residual on eligible holdout windows. Descriptive, not causal or a selection criterion.",
        "metrics": {},
    }
    for i, metric in enumerate(METRICS):
        mask = holdout.available[:, i]
        entry = {"weight": forecaster.blend_weights[metric], "windows": int(mask.sum())}
        if entry["weight"] and metric not in forecaster.linear_heads:
            model = forecaster.models[i]
            booster = model.get_booster()
            last = int(model.best_iteration) + 1
            contribs = booster.predict(
                DMatrix(holdout.x[mask]), pred_contribs=True, iteration_range=(0, last)
            )
            raw = model.predict(holdout.x[mask])
            if not np.allclose(contribs.sum(axis=1), raw, atol=1e-3, rtol=1e-4):
                raise RuntimeError("Tree contribution sum differs from saved model prediction")
            means = np.abs(contribs[:, :-1] * entry["weight"]).mean(axis=0)
            entry["residual_contributions"] = {
                name: float(value) for name, value in zip(FEATURE_NAMES, means)
            }
            entry["contribution_sum_verified"] = True
        else:
            entry["reason"] = (
                "Persistence after acceptance gate" if not entry["weight"] else "Linear head"
            )
        report["metrics"][metric] = entry
    # The first declared holdout episode and WAN1 are fixed choices, not best-run selection.
    first = prepare_partition(partitions["holdout"][:1], dataset.sampling)
    first_pred = forecaster.predict_matrix(first.x)
    # Explicitly identify the WAN and use the evaluator's measured target timestamps.
    first_series = partitions["holdout"][0].series
    if next(iter(first_series)) != "wan1":
        raise ValueError("Unexpected link order")
    trace_windows, _ = evaluation_windows(first_series["wan1"], dataset.sampling)
    count = len(trace_windows)
    output.mkdir(parents=True)
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 5.5), constrained_layout=True)
    names = [
        "RTT (ms)",
        "Jitter (ms)",
        "Loss (percentage points)",
        "Utilization (percentage points)",
    ]
    rows = []
    for i, (ax, label) in enumerate(zip(axes.flat, names)):
        mask = first.available[:count, i]
        x = np.asarray(
            [w["target_timestamp"] - first_series["wan1"][0].timestamp for w in trace_windows]
        )[mask]
        actual = first.y[:count, i][mask]
        candidate = first_pred[:count, i][mask]
        persistence = first.x[:count, i * 6][mask]
        ax.plot(x, actual, "o-", label="Measured at t+30", color="#222222", markersize=3)
        ax.plot(x, candidate, "s-", label="Optimized forecast", color="#16776b", markersize=3)
        ax.plot(x, persistence, "--", label="Latest-value forecast", color="#b37322")
        ax.set_title(label, fontsize=11)
        ax.set_xlabel("Target time from episode start (s)", fontsize=9)
        ax.grid(alpha=0.2)
        for t, y, p, b in zip(x, actual, candidate, persistence):
            rows.append(
                {
                    "metric": METRICS[i],
                    "target_offset_seconds": float(t),
                    "actual": float(y),
                    "candidate": float(p),
                    "persistence": float(b),
                }
            )
    axes.flat[0].legend(fontsize=7, loc="best")
    fig.suptitle("First predeclared holdout episode / WAN1 / real Linux veth packets", fontsize=12)
    fig.savefig(output / "forecast-trace.png", dpi=200)
    plt.close(fig)
    report["trace"] = {
        "episode": holdout_ids[0],
        "wan": "wan1",
        "alignment": "Forecast at t compared with measurement at t+30; measured target is never used as an input.",
        "rows": rows,
    }
    # A legible two-panel version for the short Word appendix.
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.1), constrained_layout=True)
    for ax, metric, label in zip(
        axes, ("latency_ms", "utilization_pct"), ("RTT (ms)", "Utilization (percentage points)")
    ):
        selected = [row for row in rows if row["metric"] == metric]
        times = [row["target_offset_seconds"] for row in selected]
        for key, title, style, color in (
            ("actual", "Measured at t+30", "o-", "#222222"),
            ("candidate", "Optimized forecast", "s-", "#16776b"),
            ("persistence", "Latest value", "--", "#b37322"),
        ):
            ax.plot(times, [row[key] for row in selected], style, color=color, label=title)
        ax.set_title(label, fontsize=14)
        ax.set_xlabel("Target time from episode start (s)", fontsize=12)
        ax.tick_params(labelsize=12)
        ax.grid(alpha=0.2)
    axes[0].legend(fontsize=10)
    fig.savefig(output / "forecast-trace-compact.png", dpi=200)
    plt.close(fig)
    active = [(m, e) for m, e in report["metrics"].items() if "residual_contributions" in e]
    fig, axes = plt.subplots(1, len(active), figsize=(11.5, 3.2), constrained_layout=True)
    for ax, (metric, entry), label in zip(
        np.atleast_1d(axes), active, [names[METRICS.index(m)] for m, _ in active]
    ):
        ordered = sorted(entry["residual_contributions"].items(), key=lambda p: p[1])[-5:]
        ax.barh([k for k, _ in ordered], [v for _, v in ordered], color="#16776b")
        ax.set_title(label, fontsize=11)
        ax.tick_params(axis="y", labelsize=7)
        ax.set_xlabel("Mean absolute residual contribution", fontsize=8)
        ax.grid(axis="x", alpha=0.2)
    fig.suptitle("Saved XGBoost model / exact tree contributions / held-out data", fontsize=12)
    fig.savefig(output / "feature-contributions.png", dpi=200)
    plt.close(fig)
    (output / "explanation.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = explain(args.dataset, args.model, args.output)
    print(json.dumps({"model_sha256": result["model_sha256"], "output": str(args.output)}))

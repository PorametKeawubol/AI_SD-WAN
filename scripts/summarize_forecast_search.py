"""Assemble completed forecast evidence without selecting or changing any model."""

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sdwan.domain import METRICS
from sdwan.model import Forecaster, fingerprint
from sdwan.optimization import prepare_partition, validate_dataset

ROOT = Path(__file__).resolve().parents[1]
DATE = "20261017"


def read(path):
    return json.loads((ROOT / path).read_text())


def main():
    campaign = ROOT / f"artifacts/link-campaign-{DATE}"
    status = json.loads((campaign / "status.json").read_text())
    if (
        not status["completed"]
        or not status["cleanup_complete"]
        or status["episodes_completed"] != 12
        or status["errors"]
    ):
        raise RuntimeError("Only a fully completed and cleaned campaign may be summarized")
    destination = ROOT / f"reports/forecast-search-{DATE}"
    destination.mkdir(exist_ok=False)
    reports = {
        name: read(f"artifacts/model-search-{suffix}-{DATE}/report/search.json")
        for name, suffix in (
            ("synthetic", "synthetic"),
            ("previous_flexiedge_exploratory", "real-exploratory"),
            ("new_link_packets", "link"),
        )
    }
    dataset = validate_dataset(json.loads((campaign / "dataset.json").read_text()))
    partition = prepare_partition(dataset.episodes[-3:], dataset.sampling)
    candidate = Forecaster(ROOT / f"artifacts/model-search-link-{DATE}")
    frozen = Forecaster(ROOT / "artifacts/model")
    current = partition.x[:, [0, 6, 12, 18]]
    trend = np.maximum(0, current + 30 * partition.x[:, [5, 11, 17, 23]])
    trend[:, 2:] = np.clip(trend[:, 2:], 0, 100)
    predictions = {
        "persistence": current,
        "linear_trend": trend,
        "frozen": frozen.predict_matrix(partition.x),
        "candidate": candidate.predict_matrix(partition.x),
    }
    comparison = {}
    for index, metric in enumerate(METRICS):
        mask = partition.available[:, index]
        truth, groups = partition.y[mask, index], partition.groups[mask]
        errors = {name: np.abs(values[mask, index] - truth) for name, values in predictions.items()}
        differences = np.asarray(
            [
                (
                    errors["candidate"][groups == group] - errors["persistence"][groups == group]
                ).mean()
                for group in sorted(set(groups))
            ]
        )
        rng = np.random.default_rng(20261700 + index)
        distribution = rng.choice(differences, (2000, len(differences))).mean(axis=1)
        comparison[metric] = {
            "samples": int(mask.sum()),
            "episode_groups": len(differences),
            "mae": {name: float(error.mean()) for name, error in errors.items()},
            "candidate_gain_vs_persistence": float(
                1 - errors["candidate"].mean() / errors["persistence"].mean()
            )
            if errors["persistence"].mean()
            else None,
            "paired_episode_mean_difference": float(differences.mean()),
            "descriptive_bootstrap_95_ci": np.percentile(distribution, [2.5, 97.5]).tolist(),
        }
    balanced = {
        name: read(f"reports/search-balanced-{name}-{DATE}/results.json")
        for name in ("candidate", "frozen")
    }
    summary = {
        "date": "2026-10-17",
        "runtime_promoted": False,
        "provenance": {
            "new_link_packets": "Real ICMP and UDP packets through isolated Linux veth/NetEm links; no flexiEdge/VPP/overlay in path; not ISP data",
            "previous_flexiedge_exploratory": "Already inspected historical flexiEdge probes; not new confirmation",
            "synthetic": "115 simulator episodes from predeclared seed block310000; no packets",
        },
        "campaign": {
            "status": status,
            "protocol": read(f"artifacts/link-campaign-{DATE}/protocol.json"),
            "dataset_sha256": dataset.sha256,
            "raw_frames": sum(
                len(rows) for episode in dataset.episodes for rows in episode.series.values()
            ),
            "total_probe_packets_offered": 90000,
        },
        "new_link_holdout": comparison,
        "searches": {
            name: {
                key: report[key]
                for key in (
                    "model_sha256",
                    "partition_episodes",
                    "metrics",
                    "selected_parameters",
                    "blend_weights",
                    "window_counters",
                    "frozen_baseline",
                )
            }
            for name, report in reports.items()
        },
        "balanced": {
            name: {
                key: report[key]
                for key in (
                    "model_sha256",
                    "trial_rows",
                    "heldout_prediction_mae",
                    "heldout_persistence_mae",
                    "counts",
                    "comparisons",
                )
            }
            for name, report in balanced.items()
        },
        "frozen_sha256_after": fingerprint(ROOT / "artifacts/model"),
        "limitations": [
            "Three acceptance and three holdout episode groups are a small single-machine sample, even though seeds and conditions vary.",
            "The new packet experiment tests link forecasting only; utilization is veth TX rate and cannot validate VPP counter compatibility.",
            "The scenario schedule and seeds are never model features; only measured histories are supplied.",
            "No holdout result selects parameters, weights or another candidate.",
            "Forecast MAE gains alone do not demonstrate application, routing or SLA improvement.",
            "KVM was unavailable; no new end-to-end flexiEdge packet trial was possible on this date.",
            "Historical GUI images are from the10October recording; no new VSCode screenshot was obtained.",
        ],
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    labels = (
        "RTT (ms)",
        "RTT stddev (ms)",
        "Loss (percentage points)",
        "Utilization (percentage points)",
    )
    fig, axes = plt.subplots(2, 2, figsize=(10, 6.2), layout="constrained")
    colors = ("#777777", "#bb8a2b", "#6785a0", "#17644c")
    for index, axis in enumerate(axes.flat):
        values = comparison[METRICS[index]]["mae"]
        axis.bar(range(4), list(values.values()), color=colors)
        axis.set_xticks(range(4), ["Last value", "Trend", "Frozen ML", "Candidate"], fontsize=9)
        axis.set_title(labels[index], fontsize=12)
        axis.set_ylabel("MAE")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.15)
        axis.set_axisbelow(True)
        for x, value in enumerate(values.values()):
            axis.text(x, value, f"{value:.3f}", ha="center", va="bottom", fontsize=9)
        axis.margins(y=0.22)
    fig.suptitle("Measured link-only experiment: sealed holdout, 3 episodes", fontsize=13)
    fig.savefig(destination / "link-holdout-mae.png", dpi=220)
    plt.close(fig)
    # Predeclared first holdout episode, all WANs and all metrics; no best-run selection.
    first = dataset.episodes[9]
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.6), layout="constrained")
    for index, axis in enumerate(axes.flat):
        for link, rows in first.series.items():
            axis.plot(
                [row.timestamp - rows[0].timestamp for row in rows],
                [row.vector()[index] for row in rows],
                marker=".",
                markersize=3,
                linewidth=1.2,
                label=link.upper(),
            )
        axis.set_title(labels[index], fontsize=11)
        axis.set_xlabel("Measured elapsed time (s)")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(alpha=0.15)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle(f"Actual probes and TX counters: first holdout {first.id}", fontsize=12)
    fig.savefig(destination / "first-holdout-measurements.png", dpi=220)
    plt.close(fig)
    print(json.dumps({"output": str(destination), "holdout": comparison}, indent=2))


if __name__ == "__main__":
    main()

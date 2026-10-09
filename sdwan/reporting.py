"""Post-process saved evidence without retraining or re-running any policy."""

import csv
import json
import math
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .domain import METRICS


def wilson_interval(successes, total, z=1.959963984540054):
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    half_width = (
        z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    )
    return [max(0, center - half_width), min(1, center + half_width)]


def enrich(report, rows):
    for summary in report["summary"]:
        matching = [
            r
            for r in rows
            if r["scenario"] == summary["scenario"] and r["strategy"] == summary["strategy"]
        ]
        seeds = sorted({row["seed"] for row in matching})
        successes = sum(
            all(str(row["passed"]).lower() == "true" for row in matching if row["seed"] == seed)
            for seed in seeds
        )
        summary["seed_pass_wilson_95_ci"] = wilson_interval(successes, len(seeds))
    ramp_summary = {}
    ramp_cases = {
        case["id"]
        for case in report.get("case_manifest", {}).get("cases", [])
        if case["kind"] == "ramp"
    } or {"S12"}
    for strategy in report["counts"]:
        matching = [r for r in rows if r["scenario"] in ramp_cases and r["strategy"] == strategy]
        lead = [
            float(row["proactive_lead_seconds"])
            for row in matching
            if row["proactive_lead_seconds"] not in {"", None}
        ]
        ramp_summary[strategy] = {
            "cases": sorted(ramp_cases),
            "proactive_trials": len(lead),
            "total_trials": len(matching),
            "lead_seconds_mean_when_proactive": float(np.mean(lead)) if lead else None,
            "switches_mean": float(np.mean([float(row["switches"]) for row in matching])),
        }
    report["progressive_degradation_analysis"] = ramp_summary
    return report


def plot(report, output):
    strategies = list(report["counts"])
    labels = ["Priority", "Reactive SLA", "Linear trend", "AI, no guards", "AI, guarded"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)
    handled = [
        report["counts"][s].get("handled_count", report["counts"][s].get("handled_of_16"))
        for s in strategies
    ]
    restored = [
        report["counts"][s].get("restored_count", report["counts"][s].get("restored_of_15"))
        for s in strategies
    ]
    catalogue = report.get("case_manifest")
    fault_count = catalogue["fault_case_count"] if catalogue else 16
    restored_total = catalogue["recoverable_fault_count"] if catalogue else 15
    y = np.arange(len(strategies))
    axes[0].barh(
        y - 0.18, handled, height=0.34, color="#2a7f62", label=f"Correct handling / {fault_count}"
    )
    axes[0].barh(
        y + 0.18,
        restored,
        height=0.34,
        color="#8ebcaa",
        label=f"SLA maintained / {restored_total}",
    )
    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, fault_count + 2)
    axes[0].set_xticks(
        sorted({0, fault_count // 4, fault_count // 2, 3 * fault_count // 4, fault_count})
    )
    axes[0].set_title("Fault scenario coverage")
    axes[0].set_xlabel("Cases meeting acceptance criteria")
    for index, (a, b) in enumerate(zip(handled, restored)):
        axes[0].text(a + 0.15, index - 0.18, str(a), va="center", fontsize=10)
        axes[0].text(b + 0.15, index + 0.18, str(b), va="center", fontsize=10)
    axes[0].legend(loc="upper left", bbox_to_anchor=(0, -0.17), fontsize=9, frameon=False)
    ratios = [
        report["heldout_prediction_mae"][m] / report["heldout_persistence_mae"][m] for m in METRICS
    ]
    x = np.arange(4)
    colors = [
        "#b76356" if v > 1 + 1e-6 else "#2a7f62" if v < 1 - 1e-6 else "#8c969d" for v in ratios
    ]
    axes[1].bar(x, ratios, color=colors, width=0.6)
    axes[1].axhline(1, color="#59636b", linestyle="--", label="Persistence baseline = 1")
    axes[1].set_xticks(x, ["Latency", "Jitter", "Loss*", "Utilization"])
    axes[1].set_ylim(0, max(1.4, max(ratios) * 1.2))
    axes[1].set_title("Forecast MAE on evaluation fault traces")
    axes[1].set_ylabel("Evaluated forecaster / persistence (lower is better)")
    for index, ratio in enumerate(ratios):
        axes[1].text(index, ratio + 0.03, f"{ratio:.3f}", ha="center")
    axes[1].legend(loc="lower right", fontsize=9)
    fig.suptitle(
        f"Three-link SD-WAN · synthetic replay · {report['repetitions']} seeds · 3 SLA profiles",
        fontsize=13,
    )
    fig.text(
        0.52,
        -0.03,
        "*Loss prediction falls back to persistence after validation gating.",
        fontsize=9,
    )
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(Path(output) / "evaluation.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def postprocess(output="reports/latest"):
    from .evaluation import write_report

    output = Path(output)
    report = json.loads((output / "results.json").read_text(encoding="utf-8"))
    with (output / "trials.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    enrich(report, rows)
    (output / "results.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_report(report, output)
    plot(report, output)
    return report

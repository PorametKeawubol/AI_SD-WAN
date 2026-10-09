import csv
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from .domain import (
    INTERVAL,
    LINKS,
    METRICS,
    PROFILES,
    STALE_AFTER,
    WINDOW,
    Telemetry,
    available_mbps,
    meets_sla,
)
from .features import features, regular_history
from .model import Forecaster, fingerprint
from .policy import Controller, Guardrails
from .scenarios import manifest, suite_cases
from .simulator import trace

STRATEGIES = ("priority", "reactive", "linear_trend", "predictive_unguarded", "predictive")
PASS_CRITERIA = {
    "event_sla_attainment_min": 0.98,
    "maximum_recovery_seconds": 15,
    "unsafe_selections_after_grace": 0,
    "false_no_path_ticks": 0,
    "all_down_detection_seconds_max": INTERVAL,
    "scenario_trial_pass_rate_min": 0.95,
    "normal_control_switches_max": 2,
    "notes": "SLA measured only when a true healthy path exists; first 10s of each incident excluded from SLA/safety. All-down is safe handling, not connectivity restoration.",
}


def observed_inputs(frames, model):
    latest, histories = {}, {link: [] for link in LINKS}
    snapshots, entries, matrix, linear, quarantined = [], [], [], [], 0
    for tick, frame in enumerate(frames):
        tick_linear = {}
        for row in frame.observed:
            try:
                sample = Telemetry.model_validate(row)
            except ValidationError:
                quarantined += 1
                continue
            if sample.timestamp > frame.now or (
                sample.link_id in latest and sample.timestamp <= latest[sample.link_id].timestamp
            ):
                quarantined += 1
                continue
            latest[sample.link_id] = sample
            history = histories[sample.link_id]
            history.append(sample)
            del history[:-WINDOW]
            if regular_history(history):
                matrix.append(features(history))
                entries.append((tick, sample.link_id))
                slope = (np.asarray(history[-1].vector()) - np.asarray(history[0].vector())) / (
                    history[-1].timestamp - history[0].timestamp
                )
                estimate = np.maximum(0, np.asarray(sample.vector()) + slope * 30)
                estimate[2:] = np.clip(estimate[2:], 0, 100)
                tick_linear[sample.link_id] = estimate.tolist()
        snapshots.append(dict(latest))
        linear.append(tick_linear)
    forecasts = [{} for _ in frames]
    if matrix:
        predictions = model.predict_matrix(np.asarray(matrix))
        for (tick, link), values in zip(entries, predictions):
            forecasts[tick][link] = values.tolist()
    return snapshots, forecasts, linear, quarantined


def trial(frames, snapshots, forecasts, profile, strategy):
    controller = Controller(profile, "predictive" if strategy == "linear_trend" else strategy)
    switches = 0
    unsafe = 0
    false_no_path = 0
    sla_ticks = 0
    feasible_ticks = 0
    event_available = 0
    event_ticks = 0
    latencies, losses, capacities, timings = [], [], [], []
    active_episode_start = None
    recovery_start = None
    recovery_delays = []
    all_down_delay = None
    proactive_lead = None
    first_breach = next(
        (
            frame.now
            for frame in frames
            if frame.event and not meets_sla(frame.truth["wan1"], profile)
        ),
        None,
    )
    last_selected = "wan1"
    first_event = next((f.now for f in frames if f.event), None)
    for tick, frame in enumerate(frames):
        begin = time.perf_counter()
        proposal = controller.propose(snapshots[tick], frame.now, forecasts[tick])
        if proposal.action == "switch":
            switches += 1
            controller.acknowledge(proposal.target, frame.now)
            if (
                first_breach is not None
                and first_event <= frame.now < first_breach
                and proposal.source == "wan1"
                and proactive_lead is None
            ):
                proactive_lead = first_breach - frame.now
        selected = None if proposal.action == "no_path" else controller.current
        timings.append((time.perf_counter() - begin) * 1000)
        if not frame.event:
            if recovery_start is not None:
                recovery_delays.append(frame.now - recovery_start)
            last_selected = selected
            active_episode_start = None
            recovery_start = None
            continue
        if active_episode_start is None:
            active_episode_start = frame.now
        in_grace = frame.now - active_episode_start < STALE_AFTER
        available = selected is not None and frame.truth[selected].up
        event_ticks += 1
        event_available += int(available)
        if available:
            sample = frame.truth[selected]
            latencies.append(sample.latency_ms)
            losses.append(sample.loss_pct)
            capacities.append(available_mbps(sample))
        else:
            capacities.append(0.0)
        usable = {k: v for k, v in snapshots[tick].items() if v.up and v.fresh(frame.now)}
        if selected is None and usable:
            false_no_path += 1
        if not in_grace and selected is not None and selected not in usable:
            unsafe += 1
        true_feasible = any(meets_sla(sample, profile) for sample in frame.truth.values())
        served = available and meets_sla(frame.truth[selected], profile)
        if true_feasible and not in_grace:
            feasible_ticks += 1
            sla_ticks += int(served)
        # Response starts when the PREVIOUSLY selected route becomes unusable or violates SLA.
        previous_good = last_selected is not None and meets_sla(frame.truth[last_selected], profile)
        if not previous_good and true_feasible and recovery_start is None:
            recovery_start = frame.now
        if recovery_start is not None and served:
            recovery_delays.append(frame.now - recovery_start)
            recovery_start = None
        if (
            not any(s.up for s in frame.truth.values())
            and selected is None
            and all_down_delay is None
        ):
            all_down_delay = frame.now - active_episode_start
        last_selected = selected
    if recovery_start is not None:
        recovery_delays.append(frames[-1].now - recovery_start)
    sla_rate = sla_ticks / feasible_ticks if feasible_ticks else None
    max_recovery = max(recovery_delays, default=0.0)
    all_down = any(f.event and not any(s.up for s in f.truth.values()) for f in frames)
    passed = unsafe == 0 and false_no_path == 0 and max_recovery <= 15
    if all_down:
        passed &= all_down_delay is not None and all_down_delay <= INTERVAL
    elif sla_rate is not None:
        passed &= sla_rate >= 0.98
    elif event_ticks == 0:
        passed &= switches <= PASS_CRITERIA["normal_control_switches_max"]
    return {
        "passed": bool(passed),
        "sla_attainment": sla_rate,
        "availability": event_available / event_ticks if event_ticks else 1.0,
        "max_recovery_seconds": max_recovery,
        "all_down_detection_seconds": all_down_delay,
        "switches": switches,
        "unsafe_selections": unsafe,
        "false_no_path_ticks": false_no_path,
        "latency_ms_mean": float(np.mean(latencies)) if latencies else None,
        "loss_pct_mean": float(np.mean(losses)) if losses else None,
        "capacity_proxy_mbps_mean": float(np.mean(capacities)) if capacities else None,
        "policy_cpu_ms_p95": float(np.percentile(timings, 95)),
        "proactive_lead_seconds": proactive_lead,
    }


def bootstrap_interval(values, seed=2026):
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return [float(values.mean()), float(values.mean())]
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(2000, len(values)), replace=True).mean(axis=1)
    return [float(v) for v in np.percentile(means, [2.5, 97.5])]


def evaluate(
    model_dir="artifacts/model",
    output="reports/latest",
    repetitions=30,
    seed_base=70000,
    *,
    suite="balanced",
):
    if repetitions < 1 or seed_base < 10000:
        raise ValueError("repetitions >= 1 and independent evaluation seed_base >= 10000 required")
    model = Forecaster(model_dir)
    frozen_hash = fingerprint(model_dir)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Existing evaluation output preserved; choose a fresh directory")
    output.mkdir(parents=True, exist_ok=True)
    cases = suite_cases(suite)
    catalogue = manifest(suite)
    rows, forecast_errors, persistence_errors = [], [], []
    quarantine_total = 0
    for scenario in cases:
        for repetition in range(repetitions):
            seed = seed_base + repetition
            frames = trace(scenario, seed)
            snapshots, forecasts, linear, quarantined = observed_inputs(frames, model)
            quarantine_total += quarantined
            for tick, predictions in enumerate(forecasts[:-6]):
                for link, prediction in predictions.items():
                    target = np.asarray(frames[tick + 6].truth[link].vector())
                    forecast_errors.append(np.abs(np.asarray(prediction) - target))
                    persistence_errors.append(
                        np.abs(np.asarray(snapshots[tick][link].vector()) - target)
                    )
            for profile in PROFILES.values():
                for strategy in STRATEGIES:
                    result = trial(
                        frames,
                        snapshots,
                        linear if strategy == "linear_trend" else forecasts,
                        profile,
                        strategy,
                    )
                    rows.append(
                        {
                            "scenario": scenario.id,
                            "group": scenario.group,
                            "profile": profile.name,
                            "strategy": strategy,
                            "seed": seed,
                            **result,
                        }
                    )
    summary = []
    for scenario in cases:
        for strategy in STRATEGIES:
            chosen = [
                row
                for row in rows
                if row["scenario"] == scenario.id and row["strategy"] == strategy
            ]
            # A seed is the resampling unit; application profiles are correlated within a seed.
            seed_passes = [
                all(row["passed"] for row in chosen if row["seed"] == seed_base + rep)
                for rep in range(repetitions)
            ]
            pass_rate = float(np.mean(seed_passes))
            sla = [row["sla_attainment"] for row in chosen if row["sla_attainment"] is not None]
            summary.append(
                {
                    "scenario": scenario.id,
                    "name": scenario.name,
                    "group": scenario.group,
                    "strategy": strategy,
                    "recoverable": scenario.recoverable,
                    "pass_rate": pass_rate,
                    "passed": pass_rate >= PASS_CRITERIA["scenario_trial_pass_rate_min"],
                    "sla_attainment_mean": float(np.mean(sla)) if sla else None,
                    "switches_mean": float(np.mean([r["switches"] for r in chosen])),
                    "recovery_seconds_max": max(r["max_recovery_seconds"] for r in chosen),
                }
            )
    comparisons = {}
    for baseline in ("priority", "reactive", "linear_trend"):
        paired_differences = []
        for rep in range(repetitions):
            seed_rows = [
                r
                for r in rows
                if r["seed"] == seed_base + rep and r["scenario"] not in {"CONTROL", "S07"}
            ]
            ai = np.mean([r["sla_attainment"] for r in seed_rows if r["strategy"] == "predictive"])
            other = np.mean([r["sla_attainment"] for r in seed_rows if r["strategy"] == baseline])
            paired_differences.append(float(ai - other))
        comparisons[baseline] = {
            "paired_sla_difference": float(np.mean(paired_differences)),
            "bootstrap_95_ci_by_seed": bootstrap_interval(paired_differences),
        }
    counts = {
        strategy: {
            "handled_count": sum(
                r["passed"]
                for r in summary
                if r["strategy"] == strategy and r["scenario"] != "CONTROL"
            ),
            "fault_cases_total": catalogue["fault_case_count"],
            "restored_count": sum(
                r["passed"]
                for r in summary
                if r["strategy"] == strategy and r["scenario"] not in {"CONTROL", "S07"}
            ),
            "recoverable_cases_total": catalogue["recoverable_fault_count"],
            "normal_control_passed": next(
                r["passed"]
                for r in summary
                if r["strategy"] == strategy and r["scenario"] == "CONTROL"
            ),
        }
        for strategy in STRATEGIES
    }
    for strategy, count in counts.items():
        count["by_group"] = {
            group: {
                "case_count": total,
                "passed_case_count": sum(
                    row["passed"]
                    for row in summary
                    if row["strategy"] == strategy and row["group"] == group
                ),
            }
            for group, total in catalogue["group_counts"].items()
        }
    errors, persistent = np.asarray(forecast_errors), np.asarray(persistence_errors)
    report = {
        "case_manifest": catalogue,
        "data_kind": "synthetic exogenous replay; not live flexiWAN or measured packet throughput",
        "repetitions": repetitions,
        "profiles": list(PROFILES),
        "trial_rows": len(rows),
        "evaluation_seeds": [seed_base + i for i in range(repetitions)],
        "model_sha256": frozen_hash,
        "model_metadata": model.metadata,
        "guardrails": asdict(Guardrails()),
        "pass_criteria": PASS_CRITERIA,
        "counts": counts,
        "summary": summary,
        "comparisons": comparisons,
        "quarantined_rows": quarantine_total,
        "heldout_prediction_mae": {
            metric: float(errors[:, i].mean()) for i, metric in enumerate(METRICS)
        },
        "heldout_persistence_mae": {
            metric: float(persistent[:, i].mean()) for i, metric in enumerate(METRICS)
        },
        "limitations": [
            "Only the 8 binary Up/Down states are exhaustive; other impairment cases are selected tests.",
            "Initial priority is WAN1; targeting WAN2/WAN3 also exercises standby handling, not guaranteed active-path failover.",
            "No real network, controller job latency, traffic feedback, or flow/session migration is modeled.",
            "Available bandwidth is a capacity proxy, not measured goodput.",
            "Sudden faults have no guaranteed predictive precursors.",
            "Safety and failover gains partly come from guardrails, not from ML.",
            "S07 cannot restore connectivity; it only measures correct no-path detection.",
            "Scenario pass requires every application profile to pass for >=95% of seeds; uncertainty is resampled by seed.",
        ],
    }
    if fingerprint(model_dir) != frozen_hash:
        raise RuntimeError("model changed during evaluation")
    (output / "results.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    with (output / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    from .reporting import postprocess

    return postprocess(output)


def write_report(report, output):
    catalogue = report.get("case_manifest")
    fault_count = catalogue["fault_case_count"] if catalogue else 16
    restored_total = catalogue["recoverable_fault_count"] if catalogue else 15
    case_count = catalogue["case_count"] if catalogue else 17
    lines = [
        "# Evaluation results",
        "",
        "Synthetic telemetry replay. Actual packet evidence is reported separately.",
        "",
        f"{report['repetitions']} independent seeds x 3 application profiles x {case_count} cases ({fault_count} faults + normal) x 5 strategies.",
        f"Total controller trials: {report['trial_rows']}. Frozen model SHA-256: `{report['model_sha256']}`.",
        "",
        "## How many scenarios can the system handle?",
        "",
        f"| Strategy | Correct handling / {fault_count} | SLA maintained / {restored_total} | Normal control |",
        "|---|---:|---:|---|",
    ]
    for strategy, count in report["counts"].items():
        lines.append(
            f"| {strategy} | {count.get('handled_count', count.get('handled_of_16'))} | {count.get('restored_count', count.get('restored_of_15'))} | {count['normal_control_passed']} |"
        )
    if catalogue:
        lines += [
            "",
            "## Coverage by test group",
            "",
            "| Strategy | Group | Passed / selected cases |",
            "|---|---|---|",
        ]
        for strategy, count in report["counts"].items():
            for group, totals in count["by_group"].items():
                lines.append(
                    f"| {strategy} | {group} | {totals['passed_case_count']} / {totals['case_count']} |"
                )
    lines.extend(
        [
            "",
            "S07 contributes only correct all-down handling, never connectivity recovery.",
            "",
            "## Scenario breakdown",
            "",
            "| Case | AI pass rate | AI | Reactive | Priority | Linear forecast |",
            "|---|---:|---|---|---|---|",
        ]
    )
    case_names = {row["scenario"]: row["name"] for row in report["summary"]}
    for identifier, name in case_names.items():
        records = {r["strategy"]: r for r in report["summary"] if r["scenario"] == identifier}
        status = [
            "PASS" if records[s]["passed"] else "FAIL"
            for s in ("predictive", "reactive", "priority", "linear_trend")
        ]
        lines.append(
            f"| {identifier} {name} | {records['predictive']['pass_rate']:.1%} | "
            + " | ".join(status)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Held-out forecasting MAE",
            "",
            "| Metric | Deployed forecaster | Persistence |",
            "|---|---:|---:|",
        ]
    )
    for metric in METRICS:
        lines.append(
            f"| {metric} | {report['heldout_prediction_mae'][metric]:.4f} | {report['heldout_persistence_mae'][metric]:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Paired SLA difference (AI minus baseline)",
            "",
            "CI resamples entire seeds, retaining within-seed scenario/application correlation.",
            "",
        ]
    )
    for name, comparison in report["comparisons"].items():
        lo, hi = comparison["bootstrap_95_ci_by_seed"]
        lines.append(
            f"- {name}: {comparison['paired_sla_difference']:+.4%}, bootstrap 95% CI [{lo:+.4%}, {hi:+.4%}]."
        )
    if "progressive_degradation_analysis" in report:
        lines.extend(
            [
                "",
                "## Progressive degradation tests",
                "",
                "Proactive means the selected path changed before WAN1 breached its SLA. Reactive quality scoring can also do this without forecasting.",
                "",
                "| Strategy | Proactive trials | Mean lead when proactive (seconds) | Mean path changes |",
                "|---|---:|---:|---:|",
            ]
        )
        for strategy, analysis in report["progressive_degradation_analysis"].items():
            lead = analysis["lead_seconds_mean_when_proactive"]
            lines.append(
                f"| {strategy} | {analysis['proactive_trials']}/{analysis['total_trials']} | "
                f"{f'{lead:.2f}' if lead is not None else 'N/A'} | {analysis['switches_mean']:.3f} |"
            )
        lines.extend(
            [
                "",
                "## Reading the evidence",
                "",
                "Read passing cases and paired differences from this run; case coverage alone does not establish a routing/SLA advantage from ML.",
                "The evaluated forecaster uses XGBoost only for validation-approved metrics; loss falls back to persistence in this run.",
                "Forecast metrics can deteriorate on held-out fault traces even when validation improves. Model selection is not changed after seeing evaluation results.",
                "Wilson intervals are computed from this run's seed count. A smoke run or a perfect observed pass rate does not establish production reliability.",
                "",
                "![Evaluation overview](evaluation.png)",
            ]
        )
    lines.extend(
        [
            "",
            "## Acceptance rules",
            "",
            "```json",
            json.dumps(report["pass_criteria"], indent=2),
            "```",
            "",
            "Policy CPU timing in trials.csv excludes ML inference and network/API delay; simulated detection resolution is 5 seconds.",
            "Grace excludes only the first 10 seconds of an incident from SLA/safety scoring; recovery time is still reported independently.",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {line}" for line in report["limitations"])
    lines.extend(
        ["", "Machine-readable evidence: results.json; per-trial evidence: trials.csv.", ""]
    )
    (output / "EVALUATION.md").write_text("\n".join(lines), encoding="utf-8")

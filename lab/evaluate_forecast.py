"""Score frozen weights against recorded real probes, without training or promotion."""

import argparse
import hashlib
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from sdwan.domain import HORIZON, INTERVAL, LINKS, METRICS, WINDOW, Telemetry
from sdwan.features import features, regular_history, resample_live_history
from sdwan.model import Forecaster


def measured_series(states):
    """Deduplicate API views of the same measurement; conflicting values are errors."""
    samples = {link: {} for link in LINKS}
    for frame in states:
        for link, row in frame.get("state", {}).get("telemetry", {}).items():
            if link not in samples:
                raise ValueError("Unknown WAN in recorded probe data")
            sample = Telemetry.model_validate({key: row[key] for key in Telemetry.model_fields})
            if sample.link_id != link:
                raise ValueError("Probe WAN key and identity differ")
            previous = samples[link].get(sample.timestamp)
            if previous and previous[0] != sample:
                raise ValueError("Conflicting values for one WAN measurement timestamp")
            # Freshness is from the recorded API observation, not today's wall clock.
            fresh = row.get("fresh") is True and 0 <= row.get("age_seconds", -1) <= 10
            samples[link][sample.timestamp] = (sample, fresh or bool(previous and previous[1]))
    return {
        link: [sample for _, (sample, fresh) in sorted(rows.items()) if fresh]
        for link, rows in samples.items()
    }


def evaluation_windows(series, sampling, *, target_tolerance=0.5):
    """Whole-trial histories and t+30s labels; labels never enter feature preparation."""
    if sampling not in {"strict", "causal_linear_5s"}:
        raise ValueError("Unknown sampling contract")
    if not 0 < target_tolerance <= 1:
        raise ValueError("Target tolerance must be positive and no more than one second")
    counters, result = Counter(), []
    timestamps = [sample.timestamp for sample in series]
    if timestamps != sorted(set(timestamps)):
        raise ValueError("Expected strictly increasing unique measured timestamps")
    for index in range(WINDOW - 1, len(series)):
        counters["candidate_windows"] += 1
        current = series[index]
        history = series[max(0, index - WINDOW) : index + 1]
        if sampling == "causal_linear_5s":
            history = resample_live_history(history)
        else:
            history = history[-WINDOW:]
            if not regular_history(history) or not all(row.up for row in history):
                history = None
        if history is None:
            counters["history_rejected"] += 1
            continue
        counters["forecast_available_windows"] += 1
        target_time = current.timestamp + HORIZON * INTERVAL
        right = bisect_left(timestamps, target_time)
        candidates = [item for item in (right - 1, right) if index < item < len(series)]
        nearest = (
            min(candidates, key=lambda item: abs(timestamps[item] - target_time))
            if candidates
            else None
        )
        if nearest is None or abs(timestamps[nearest] - target_time) > target_tolerance:
            counters["future_target_missing"] += 1
            continue
        target = series[nearest]
        # RTT/stddev sentinels when no packet replies arrive are unavailable measurements.
        available = [
            target.up and target.latency_ms < 10000,
            target.up and target.jitter_ms < 5000,
            True,
            True,
        ]
        if not target.up:
            counters["future_down_windows"] += 1
        counters["scored_windows"] += 1
        result.append(
            {
                "matrix": features(history),
                "current": current.vector(),
                "target": target.vector(),
                "available_metrics": available,
                "current_timestamp": current.timestamp,
                "target_timestamp": target.timestamp,
            }
        )
    return result, dict(counters)


def metric_errors(windows, forecaster):
    if not windows:
        return {
            metric: {"samples": 0, "forecast_mae": None, "persistence_mae": None}
            for metric in METRICS
        }
    forecast = forecaster.predict_matrix(np.asarray([row["matrix"] for row in windows]))
    if forecast.shape != (len(windows), len(METRICS)) or not np.all(np.isfinite(forecast)):
        raise ValueError("Frozen model produced invalid predictions")
    result = {}
    for index, metric in enumerate(METRICS):
        eligible = [item for item, row in enumerate(windows) if row["available_metrics"][index]]
        forecast_errors = [
            abs(float(forecast[item, index]) - windows[item]["target"][index]) for item in eligible
        ]
        persistence_errors = [
            abs(windows[item]["current"][index] - windows[item]["target"][index])
            for item in eligible
        ]
        result[metric] = {
            "samples": len(eligible),
            "forecast_absolute_error_sum": sum(forecast_errors),
            "persistence_absolute_error_sum": sum(persistence_errors),
            "forecast_mae": sum(forecast_errors) / len(eligible) if eligible else None,
            "persistence_mae": sum(persistence_errors) / len(eligible) if eligible else None,
        }
    return result


def evaluate(experiments, model_directory, *, samplings=("strict", "causal_linear_5s")):
    model_directory = Path(model_directory)
    forecaster = Forecaster(model_directory)
    hashes = {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(model_directory.glob("*.json"))
    }
    rows, excluded = [], []
    observed = set()
    for directory in map(Path, experiments):
        configuration = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
        declared = configuration.get("model_files_sha256")
        if declared and declared != hashes:
            raise ValueError("Experiment weights differ from the frozen model being evaluated")
        for detail_file in sorted(directory.glob("S*/trial.json")):
            trial = detail_file.parent
            if trial.resolve() in observed:
                raise ValueError("One recorded trial was supplied more than once")
            observed.add(trial.resolve())
            detail = json.loads(detail_file.read_text(encoding="utf-8"))
            if detail.get("status") != "completed" or not detail.get(
                "packet_measurement_completed"
            ):
                excluded.append(
                    {
                        "experiment": directory.name,
                        "trial": trial.name,
                        "reason": "incomplete_packet_trial",
                    }
                )
                continue
            series = measured_series(
                json.loads((trial / "states.json").read_text(encoding="utf-8"))
            )
            for link, samples in series.items():
                for sampling in samplings:
                    windows, counts = evaluation_windows(samples, sampling)
                    rows.append(
                        {
                            "experiment": directory.name,
                            "trial": trial.name,
                            "wan": link,
                            "sampling": sampling,
                            "measured_unique_samples": len(samples),
                            "trial_passed": detail.get("result", {}).get("passed"),
                            "trial_valid": detail.get("result", {}).get("valid"),
                            "model_hashes_recorded_in_experiment": bool(declared),
                            "window_counts": counts,
                            "metrics": metric_errors(windows, forecaster),
                        }
                    )
    aggregate = defaultdict(dict)
    for sampling in samplings:
        group = [row for row in rows if row["sampling"] == sampling]
        count = Counter()
        for row in group:
            count.update(row["window_counts"])
        aggregate[sampling]["window_counts"] = dict(count)
        aggregate[sampling]["metrics"] = {}
        for metric in METRICS:
            samples = sum(row["metrics"][metric]["samples"] for row in group)
            aggregate[sampling]["metrics"][metric] = {
                "samples": samples,
                **{
                    name + "_mae": sum(
                        row["metrics"][metric].get(name + "_absolute_error_sum", 0) for row in group
                    )
                    / samples
                    if samples
                    else None
                    for name in ("forecast", "persistence")
                },
            }
    return {
        "evidence_level": "real_recorded_probe_forecast_diagnostic",
        "model_training_data_kind": forecaster.metadata["data_kind"],
        "posthoc_diagnostic": True,
        "model_promotion": False,
        "model_validated_on_real_data": False,
        "model_files_sha256": hashes,
        "horizon_seconds": HORIZON * INTERVAL,
        "target_matching_tolerance_seconds": 0.5,
        "aggregate": dict(aggregate),
        "rows": rows,
        "excluded_trials": excluded,
        "limitations": [
            "Small bring-up dataset; no independent real-data training/validation/held-out design",
            "All completed trials are included regardless of routing pass/fail; weights are never fitted here",
            "Histories and targets remain within each whole trial; future labels do not enter features",
            "API snapshots can miss observations; missing targets are reported and never filled",
            "Unavailable RTT/stddev sentinels are excluded from those metrics; loss/utilization remain measured",
            "Overlapping windows are correlated; no independent-sample significance claim",
            "Sampling comparison is an engineering diagnostic, not a model deployment acceptance test",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", nargs="+", required=True)
    parser.add_argument("--model", default="artifacts/model")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Existing diagnostic preserved; choose a fresh output")
    result = evaluate(args.experiments, args.model)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result["aggregate"], indent=2))

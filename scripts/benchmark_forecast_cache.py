"""Compare unchanged-frame forecast reads using inert storage and frozen weights."""

import argparse
import hashlib
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xgboost

from sdwan.domain import LINKS, WINDOW
from sdwan.features import resample_live_history
from sdwan.model import fingerprint
from sdwan.service import Service
from sdwan.simulator import training_episode

ROOT = Path(__file__).resolve().parents[1]


class InertStore:
    """No SQL, files, network, controller or shared state is touched."""

    def get(self, _, default=None):
        return default

    def history(self, *_):
        return []

    def log(self, *_):
        pass

    def ingest(self, *_):
        pass


def uncached_forecasts(service):
    predictions = {}
    for link, history in service.histories.items():
        samples = list(history)
        if service.forecast_sampling == "causal_linear_5s":
            samples = resample_live_history(samples)
            if samples is None:
                continue
        values = service.model.predict(samples)
        if values is not None:
            predictions[link] = values
    return predictions


def milliseconds_per_call(function, calls):
    started = time.perf_counter()
    for _ in range(calls):
        function()
    return (time.perf_counter() - started) * 1000 / calls


def benchmark(model_directory, rounds=5):
    if not 3 <= rounds <= 20:
        raise ValueError("Choose 3..20 benchmark rounds")
    service = Service(model_dir=model_directory, store=InertStore())
    if service.model is None:
        raise ValueError("Frozen model is unavailable")
    source = training_episode(300000, 0)
    for index in range(WINDOW):
        rows = [source[link][index].model_dump() for link in LINKS]
        service.ingest(rows, now=rows[0]["timestamp"])
    expected = uncached_forecasts(service)
    assert service.forecasts() == expected
    base_times, cached_times, new_frame_times = [], [], []
    for _ in range(rounds):
        # Both readers operate on exactly the same accepted frame and weights.
        base_times.append(milliseconds_per_call(lambda: uncached_forecasts(service), 20))
        cached_times.append(milliseconds_per_call(service.forecasts, 2000))
        assert service.forecasts() == uncached_forecasts(service)
        fresh_times = []
        for _ in range(20):
            rows = [
                row.model_copy(update={"timestamp": row.timestamp + 5}).model_dump()
                for row in service.latest.values()
            ]
            service.ingest(rows, now=rows[0]["timestamp"])
            started = time.perf_counter()
            fresh = service.forecasts()
            fresh_times.append((time.perf_counter() - started) * 1000)
            assert fresh == uncached_forecasts(service)
        new_frame_times.append(statistics.median(fresh_times))
    baseline_ms = statistics.median(base_times)
    cached_ms = statistics.median(cached_times)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_level": "local_same_frame_inference_microbenchmark",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "xgboost_version": xgboost.__version__,
        "numpy_version": np.__version__,
        "model_sha256": fingerprint(model_directory),
        "source_sha256": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in (
                "sdwan/service.py",
                "sdwan/model.py",
                "scripts/benchmark_forecast_cache.py",
            )
        },
        "wan_histories": len(LINKS),
        "rounds": rounds,
        "calls_per_round": {"uncached": 20, "cached": 2000, "new_frame": 20},
        "uncached_ms_per_read_median": baseline_ms,
        "cached_ms_per_read_median": cached_ms,
        "same_frame_speedup": baseline_ms / cached_ms,
        "new_frame_inference_ms_median": statistics.median(new_frame_times),
        "predictions_identical": True,
        "round_timings_ms": {
            "uncached": base_times,
            "cached": cached_times,
            "new_frame": new_frame_times,
        },
        "runtime_mutated": False,
        "limitations": [
            "Unchanged-frame reads only; newly accepted telemetry still requires inference.",
            "This is not a whole-service CPU, API latency or forecasting-accuracy benchmark.",
            "Synthetic fixed histories and inert storage; no database/controller traffic is generated.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("artifacts/model"))
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Existing benchmark evidence preserved")
    result = benchmark(args.model, args.rounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "uncached_ms_per_read_median",
                    "cached_ms_per_read_median",
                    "same_frame_speedup",
                    "new_frame_inference_ms_median",
                    "predictions_identical",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

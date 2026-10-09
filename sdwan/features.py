from bisect import bisect_right
from itertools import pairwise

import numpy as np

from .domain import INTERVAL, METRICS, WINDOW, Telemetry

FEATURE_NAMES = [
    f"{metric}_{stat}"
    for metric in METRICS
    for stat in ("current", "lag1", "lag3", "mean", "std", "trend")
]
FEATURE_NAMES += ["up", "history_fraction"]


def features(history: list[Telemetry]) -> np.ndarray:
    if not history:
        raise ValueError("empty history")
    history = history[-WINDOW:]
    values = np.asarray([sample.vector() for sample in history], dtype=np.float32)
    # Right-aligned lags use only measurements that have already arrived.
    result = []
    elapsed = max(INTERVAL, history[-1].timestamp - history[0].timestamp)
    for i in range(4):
        column = values[:, i]
        result.extend(
            [
                column[-1],
                column[max(0, len(column) - 2)],
                column[max(0, len(column) - 4)],
                column.mean(),
                column.std(),
                (column[-1] - column[0]) / elapsed,
            ]
        )
    result.extend([float(history[-1].up), len(history) / WINDOW])
    return np.asarray(result, dtype=np.float32)


def regular_history(history: list[Telemetry]) -> bool:
    return len(history) >= WINDOW and all(
        abs(b.timestamp - a.timestamp - INTERVAL) <= 0.1
        for a, b in zip(history[-WINDOW:], history[-WINDOW + 1 :])
    )


def resample_live_history(history: list[Telemetry]) -> list[Telemetry] | None:
    """Causal five-second inference grid; original measured timestamps stay untouched.

    An extra sample brackets the first grid point. Linear interpolation uses only
    samples already present at inference, with every measured interval in [4, 6]
    seconds. Gaps, down links and unknown history are not filled or extrapolated.
    This adaptation does not validate synthetic model weights on real telemetry.
    """
    history = history[-WINDOW - 1 :]
    if (
        len(history) < WINDOW
        or len({row.link_id for row in history}) != 1
        or not all(row.up for row in history)
        or any(
            not INTERVAL - 1 <= b.timestamp - a.timestamp <= INTERVAL + 1
            for a, b in pairwise(history)
        )
    ):
        return None
    times = [row.timestamp for row in history]
    first = times[-1] - (WINDOW - 1) * INTERVAL
    if first < times[0]:
        return None
    result = []
    for index in range(WINDOW):
        timestamp = first + index * INTERVAL
        left = bisect_right(times, timestamp) - 1
        if left == len(history) - 1:
            result.append(history[left].model_copy(update={"timestamp": timestamp}))
            continue
        a, b = history[left], history[left + 1]
        fraction = (timestamp - a.timestamp) / (b.timestamp - a.timestamp)
        result.append(
            Telemetry(
                link_id=a.link_id,
                timestamp=timestamp,
                up=True,
                **{
                    metric: getattr(a, metric)
                    + fraction * (getattr(b, metric) - getattr(a, metric))
                    for metric in METRICS
                },
            )
        )
    return result

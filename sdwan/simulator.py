"""Exogenous synthetic telemetry, not a packet/network emulator."""

from dataclasses import dataclass

import numpy as np

from .domain import INTERVAL, LINKS, Telemetry
from .scenarios import NORMAL, SCENARIOS, Scenario  # noqa: F401 - legacy import compatibility


@dataclass
class Frame:
    now: float
    truth: dict[str, Telemetry]
    observed: list[dict]
    event: bool


def trace(scenario: Scenario, seed: int, ticks: int = 150, epoch=1_900_000_000) -> list[Frame]:
    rng = np.random.default_rng(seed)
    baseline = np.asarray([[24, 4, 0.10, 30], [40, 7, 0.15, 40], [65, 10, 0.20, 35]])
    baseline *= rng.uniform(0.85, 1.15, (3, 4))
    noise = np.zeros((3, 4))
    start = int(rng.integers(28, 35))
    end = int(rng.integers(112, 120))
    frames = []
    for tick in range(ticks):
        now = epoch + tick * INTERVAL
        event = start <= tick < end and scenario.kind != "normal"
        noise = 0.65 * noise + rng.normal(0, [1.6, 0.5, 0.025, 1.8], (3, 4))
        values = np.maximum(0, baseline + noise)
        up = [True] * 3
        if event:
            scale = float(rng.uniform(0.9, 1.1))
            for link in scenario.links:
                idx = LINKS.index(link)
                if scenario.kind == "down":
                    up[idx] = False
                    values[idx, :3] = [1000, 200, 100]
                elif scenario.kind == "latency":
                    values[idx, 0] = 450 * scale
                elif scenario.kind == "jitter":
                    values[idx, 1] = 150 * scale
                elif scenario.kind == "loss":
                    values[idx, 2] = 8 * scale
                elif scenario.kind == "saturation":
                    values[idx, 3] = 98
                elif scenario.kind == "ramp":
                    progress = min(1, (tick - start) / 35)
                    values[idx] += np.asarray([420, 100, 5, 65]) * progress
                elif scenario.kind == "flap":
                    up[idx] = ((tick - start) // 3) % 2 == 0
                elif scenario.kind == "mixed":
                    if link == scenario.links[0]:
                        values[idx, 0] = 460 * scale
                    elif link == scenario.links[1]:
                        values[idx, 2] = 9 * scale
                elif scenario.kind == "corrupt":
                    values[idx, 0] = 480 * scale
        values[:, 2:] = np.clip(values[:, 2:], 0, 100)
        truth = {
            link: Telemetry(
                link_id=link,
                timestamp=now,
                up=up[idx],
                latency_ms=float(values[idx, 0]),
                jitter_ms=float(values[idx, 1]),
                loss_pct=float(values[idx, 2]),
                utilization_pct=float(values[idx, 3]),
            )
            for idx, link in enumerate(LINKS)
        }
        observed = []
        for link, sample in truth.items():
            if event and scenario.kind == "missing" and link in scenario.links:
                continue
            row = sample.model_dump()
            if event and scenario.kind == "corrupt" and link in scenario.links:
                row["loss_pct"] = 101.0
            observed.append(row)
        frames.append(Frame(now, truth, observed, event))
    return frames


def training_episode(seed: int, episode: int, ticks=200) -> dict[str, list[Telemetry]]:
    """Random fault processes; independent from the fixed benchmark catalogue."""
    rng = np.random.default_rng(seed)
    result = {link: [] for link in LINKS}
    for link in LINKS:
        baseline = rng.uniform([15, 2, 0.02, 20], [80, 15, 0.4, 60])
        latent = np.tile(baseline, (ticks, 1))
        up = np.ones(ticks, dtype=bool)
        cursor = 20
        while cursor < ticks - 25:
            duration = int(rng.integers(25, 65))
            stop = min(ticks, cursor + duration)
            metric = int(rng.integers(0, 5))
            amplitude = [
                rng.uniform(100, 500),
                rng.uniform(25, 180),
                rng.uniform(1, 10),
                rng.uniform(25, 65),
            ]
            if metric == 4:
                up[cursor:stop] = False
                latent[cursor:stop, :3] = [1000, 200, 100]
            else:
                shape = int(rng.integers(0, 3))
                progress = np.arange(stop - cursor) / max(1, (stop - cursor) / 2)
                if shape == 0:
                    envelope = np.ones(stop - cursor)
                elif shape == 1:
                    envelope = np.minimum(1, progress)
                else:
                    envelope = np.maximum(0, 1 - np.abs(progress - 1))
                latent[cursor:stop, metric] += amplitude[metric] * envelope
                # Congestion has correlated, measurable early symptoms.
                if metric == 3:
                    latent[cursor:stop, 0] += 160 * envelope
                    latent[cursor:stop, 1] += 25 * envelope
            cursor = stop + int(rng.integers(10, 25))
        latent += rng.normal(0, [2, 0.6, 0.04, 2], latent.shape)
        latent = np.maximum(0, latent)
        latent[:, 2:] = np.clip(latent[:, 2:], 0, 100)
        for tick, values in enumerate(latent):
            result[link].append(
                Telemetry(
                    link_id=link,
                    timestamp=1_700_000_000 + episode * ticks * INTERVAL + tick * INTERVAL,
                    up=bool(up[tick]),
                    latency_ms=float(values[0]),
                    jitter_ms=float(values[1]),
                    loss_pct=float(values[2]),
                    utilization_pct=float(values[3]),
                )
            )
    return result

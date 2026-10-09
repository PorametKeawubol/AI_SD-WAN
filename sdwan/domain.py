import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

LINKS = ("wan1", "wan2", "wan3")
METRICS = ("latency_ms", "jitter_ms", "loss_pct", "utilization_pct")
INTERVAL = 5
HORIZON = 6
WINDOW = 12
STALE_AFTER = 10


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    link_id: str
    timestamp: float = Field(ge=0)
    up: StrictBool
    latency_ms: float = Field(ge=0, le=10000)
    jitter_ms: float = Field(ge=0, le=5000)
    loss_pct: float = Field(ge=0, le=100)
    utilization_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def known_link(self):
        if self.link_id not in LINKS:
            raise ValueError("unknown WAN link")
        return self

    def fresh(self, now: float) -> bool:
        return 0 <= now - self.timestamp <= STALE_AFTER

    def vector(self) -> list[float]:
        return [getattr(self, key) for key in METRICS]


@dataclass(frozen=True)
class Link:
    capacity_mbps: float
    cost: float


LINK_CONFIG = {"wan1": Link(100, 1), "wan2": Link(80, 1.2), "wan3": Link(30, 3)}


@dataclass(frozen=True)
class Profile:
    name: str
    latency_ms: float
    jitter_ms: float
    loss_pct: float
    utilization_pct: float
    demand_mbps: float


PROFILES = {
    "voice": Profile("voice", 100, 30, 1, 90, 2),
    "interactive": Profile("interactive", 150, 40, 2, 90, 5),
    "bulk": Profile("bulk", 300, 100, 3, 90, 10),
}


def meets_sla(sample: Telemetry, profile: Profile) -> bool:
    return (
        sample.up
        and all(getattr(sample, metric) <= getattr(profile, metric) for metric in METRICS)
        and available_mbps(sample) >= profile.demand_mbps
    )


def available_mbps(sample: Telemetry) -> float:
    """A capacity/headroom proxy; this is NOT measured TCP/UDP throughput."""
    if not sample.up:
        return 0.0
    return LINK_CONFIG[sample.link_id].capacity_mbps * (1 - sample.utilization_pct / 100)


def quality(values: list[float], profile: Profile, link_id: str) -> float:
    limits = [getattr(profile, metric) for metric in METRICS]
    ratios = [min(5.0, max(0, value) / limit) for value, limit in zip(values, limits)]
    weights = (0.30, 0.20, 0.35, 0.15)
    headroom = LINK_CONFIG[link_id].capacity_mbps * (1 - min(100, values[3]) / 100)
    capacity_penalty = 1.5 if headroom < profile.demand_mbps else 0.0
    # Explicit SLA penalty prevents a weighted average from hiding one bad metric.
    violations = sum(ratio > 1 for ratio in ratios)
    return sum(w * r for w, r in zip(weights, ratios)) + 0.7 * violations + capacity_penalty


def finite_predictions(values) -> bool:
    return len(values) == 4 and all(math.isfinite(float(v)) and v >= 0 for v in values)

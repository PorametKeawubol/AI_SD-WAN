from dataclasses import asdict, dataclass, field

from .domain import LINK_CONFIG, LINKS, Profile, Telemetry, finite_predictions, meets_sla, quality


@dataclass(frozen=True)
class Guardrails:
    cooldown_seconds: float = 30
    confirmations: int = 3
    improvement: float = 0.15
    prediction_weight: float = 0.35


@dataclass
class Decision:
    action: str
    source: str | None
    target: str | None
    reason: str
    scores: dict[str, float] = field(default_factory=dict)
    predicted: dict[str, list[float]] = field(default_factory=dict)
    degraded: bool = False

    def to_dict(self):
        return asdict(self)


class Controller:
    """Policy proposal is separate from acknowledgement of a successful apply."""

    def __init__(self, profile: Profile, strategy="predictive", guards=None):
        if strategy not in {"priority", "reactive", "predictive", "predictive_unguarded"}:
            raise ValueError("unknown strategy")
        self.profile = profile
        self.strategy = strategy
        self.guards = guards or Guardrails()
        self.current: str | None = "wan1"
        self.last_switch = float("-inf")
        self.candidate: str | None = None
        self.streak = 0
        self.candidate_since = float("-inf")
        self.candidate_sample_time = float("-inf")

    def reset_candidate(self):
        self.candidate = None
        self.streak = 0
        self.candidate_since = float("-inf")
        self.candidate_sample_time = float("-inf")

    def acknowledge(self, target: str | None, now: float):
        self.current = target
        self.last_switch = now
        self.reset_candidate()

    def propose(self, telemetry: dict[str, Telemetry], now: float, forecasts=None) -> Decision:
        forecasts = forecasts or {}
        usable = {k: v for k, v in telemetry.items() if v.fresh(now) and v.up}
        if not usable:
            self.reset_candidate()
            return Decision("no_path", self.current, None, "No fresh, up WAN path", degraded=True)
        scores = {}
        used_predictions = {}
        for link_id, sample in usable.items():
            score = quality(sample.vector(), self.profile, link_id)
            forecast = forecasts.get(link_id)
            if (
                self.strategy.startswith("predictive")
                and forecast is not None
                and finite_predictions(forecast)
            ):
                predicted_score = quality(forecast, self.profile, link_id)
                # A forecast can warn of deterioration but cannot excuse current SLA violations.
                score += self.guards.prediction_weight * max(0, predicted_score - score)
                used_predictions[link_id] = [float(v) for v in forecast]
            scores[link_id] = score + 0.015 * LINK_CONFIG[link_id].cost
        if self.strategy == "priority":
            best = next(link_id for link_id in LINKS if link_id in usable)
        else:
            compliant = [key for key, sample in usable.items() if meets_sla(sample, self.profile)]
            best = min(compliant or list(scores), key=lambda key: (scores[key], LINKS.index(key)))
        degraded = not any(meets_sla(sample, self.profile) for sample in usable.values())

        def decision(action, target, reason):
            return Decision(
                action, self.current, target, reason, scores, used_predictions, degraded
            )

        if self.current not in usable:
            self.reset_candidate()
            return decision("switch", best, "Current path down/stale; bypass cooldown")
        if best == self.current:
            self.reset_candidate()
            return decision("hold", self.current, "Current path remains preferred")
        if self.strategy == "priority" or self.strategy == "predictive_unguarded":
            return decision("switch", best, "Best path without stability guards")
        # A current SLA violation with a compliant alternative is an immediate brownout escape.
        if not meets_sla(usable[self.current], self.profile) and meets_sla(
            usable[best], self.profile
        ):
            self.reset_candidate()
            return decision("switch", best, "Current SLA violation; healthy alternative")
        if scores[self.current] - scores[best] < self.guards.improvement:
            self.reset_candidate()
            return decision("hold", self.current, "Improvement below hysteresis margin")
        if now - self.last_switch < self.guards.cooldown_seconds:
            self.reset_candidate()
            return decision("hold", self.current, "Cooldown active")
        if best == self.candidate:
            if (
                now - self.candidate_since >= 5
                and usable[best].timestamp > self.candidate_sample_time
            ):
                self.streak += 1
                self.candidate_since = now
                self.candidate_sample_time = usable[best].timestamp
        else:
            self.candidate, self.streak = best, 1
            self.candidate_since = now
            self.candidate_sample_time = usable[best].timestamp
        if self.streak < self.guards.confirmations:
            return decision("hold", self.current, "Waiting for consecutive confirmations")
        return decision("switch", best, "Sustained quality improvement")

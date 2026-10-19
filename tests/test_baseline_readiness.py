"""Readiness tolerance must not hide insufficient load or failed event SLA."""

from lab.run_live_eval import BASELINE_SECONDS, EVENT_END, TRIAL_SECONDS, assess
from sdwan.domain import PROFILES


def packets():
    return [
        {"second": second, "offered_mbps": 2, "received_mbps": 2,
         "received_packets": 200, "latency_ms": 20, "jitter_ms": 3, "loss_pct": 0}
        for second in range(TRIAL_SECONDS)
    ]


def test_readiness_floor_is_explicit_and_default_remains_98_percent():
    rows = packets()
    for row in rows[10:14]:
        row["jitter_ms"] = 40
    assert not assess(rows, [], "S12", PROFILES["voice"])["valid"]
    assert assess(rows, [], "S12", PROFILES["voice"], 0.90)["valid"]


def test_readiness_tolerance_keeps_offered_load_and_event_sla_gates():
    rows = packets()
    rows[15]["offered_mbps"] = 0.1
    assert not assess(rows, [], "S12", PROFILES["voice"], 0.90)["valid"]
    rows = packets()
    for row in rows[BASELINE_SECONDS + 10:EVENT_END]:
        row["loss_pct"] = 10
    result = assess(rows, [], "S12", PROFILES["voice"], 0.90)
    assert result["valid"] and not result["passed"]

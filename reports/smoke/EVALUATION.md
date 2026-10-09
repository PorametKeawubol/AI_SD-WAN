# Evaluation results

Synthetic data only. No real flexiWAN testbed was available.

3 independent seeds x 3 application profiles x 17 cases (16 faults + normal) x 5 strategies.
Total controller trials: 765. Frozen model SHA-256: `2a612a25df62cc93c7e8fa6203ae071a8cd27a5dde87d32f3e46f7cf4cea3e23`.

## How many scenarios can the system handle?

| Strategy | Correct handling / 16 | Connectivity/SLA recovery / 15 | Normal control |
|---|---:|---:|---|
| priority | 10 | 9 | True |
| reactive | 16 | 15 | True |
| linear_trend | 16 | 15 | True |
| predictive_unguarded | 16 | 15 | False |
| predictive | 16 | 15 | True |

S07 contributes only correct all-down handling, never connectivity recovery.

## Scenario breakdown

| Case | AI pass rate | AI | Reactive | Priority | Linear forecast |
|---|---:|---|---|---|---|
| S01 WAN1 down | 100.0% | PASS | PASS | PASS | PASS |
| S02 WAN2 down | 100.0% | PASS | PASS | PASS | PASS |
| S03 WAN3 down | 100.0% | PASS | PASS | PASS | PASS |
| S04 WAN1 + WAN2 down | 100.0% | PASS | PASS | PASS | PASS |
| S05 WAN1 + WAN3 down | 100.0% | PASS | PASS | PASS | PASS |
| S06 WAN2 + WAN3 down | 100.0% | PASS | PASS | PASS | PASS |
| S07 All links down | 100.0% | PASS | PASS | PASS | PASS |
| S08 High latency | 100.0% | PASS | PASS | FAIL | PASS |
| S09 High jitter | 100.0% | PASS | PASS | FAIL | PASS |
| S10 Packet loss | 100.0% | PASS | PASS | FAIL | PASS |
| S11 Saturation | 100.0% | PASS | PASS | FAIL | PASS |
| S12 Progressive degradation | 100.0% | PASS | PASS | FAIL | PASS |
| S13 Link flapping | 100.0% | PASS | PASS | PASS | PASS |
| S14 Concurrent mixed impairments | 100.0% | PASS | PASS | FAIL | PASS |
| S15 Missing/stale telemetry | 100.0% | PASS | PASS | PASS | PASS |
| S16 Corrupt telemetry | 100.0% | PASS | PASS | PASS | PASS |
| CONTROL Normal control | 100.0% | PASS | PASS | PASS | PASS |

## Held-out forecasting MAE

| Metric | XGBoost | Persistence |
|---|---:|---:|
| latency_ms | 101.8715 | 26.1084 |
| jitter_ms | 22.9517 | 5.3968 |
| loss_pct | 10.7197 | 2.2632 |
| utilization_pct | 3.3973 | 2.8678 |

## Paired SLA difference (AI minus baseline)

CI resamples entire seeds, retaining within-seed scenario/application correlation.

- priority: +39.1223%, bootstrap 95% CI [+39.0765%, +39.1473%].
- reactive: +0.0000%, bootstrap 95% CI [+0.0000%, +0.0000%].
- linear_trend: +0.0000%, bootstrap 95% CI [+0.0000%, +0.0000%].

## Acceptance rules

```json
{
  "event_sla_attainment_min": 0.98,
  "maximum_recovery_seconds": 15,
  "unsafe_selections_after_grace": 0,
  "false_no_path_ticks": 0,
  "all_down_detection_seconds_max": 5,
  "scenario_trial_pass_rate_min": 0.95,
  "normal_control_switches_max": 2,
  "notes": "SLA measured only when a true healthy path exists; first 10s of each incident excluded from SLA/safety. All-down is safe handling, not connectivity restoration."
}
```

Policy CPU timing in trials.csv excludes ML inference and network/API delay; simulated detection resolution is 5 seconds.
Grace excludes only the first 10 seconds of an incident from SLA/safety scoring; recovery time is still reported independently.

## Limitations

- 16 is a selected fault catalogue, not an exhaustive count of three-link failures.
- No real network, controller job latency, traffic feedback, or flow/session migration is modeled.
- Available bandwidth is a capacity proxy, not measured goodput.
- Sudden faults have no guaranteed predictive precursors.
- Safety and failover gains partly come from guardrails, not from ML.
- S07 cannot restore connectivity; it only measures correct no-path detection.
- Scenario pass requires every application profile to pass for >=95% of seeds; uncertainty is resampled by seed.

Machine-readable evidence: results.json; per-trial evidence: trials.csv.

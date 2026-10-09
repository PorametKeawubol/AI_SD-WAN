# Balanced scenario design — 2026-10-02

The current experiment catalogue separates the complete binary state space from sampled impairment tests. Three Up/Down links have exactly eight states. The **balanced v2 catalogue has 38 selected tests**, comprising 37 fault cases and one normal CONTROL.

| Group | Coverage rule | Cases |
|---|---|---:|
| Availability | All 2³ Up/Down states, including all-up CONTROL | 8 |
| Quality | Five kinds (latency/jitter/loss/saturation/ramp) on each of three WANs | 15 |
| Stability | Flapping on each WAN | 3 |
| Mixed | Latency on one WAN and loss on another; all six ordered pairs | 6 |
| Telemetry | Missing/stale or corrupt samples on each WAN | 6 |
| Total | 37 faults + CONTROL | 38 |

Only the binary state space is exhaustive. Severity values, chronology and other impairment combinations remain selected experimental choices. XGBoost forecasts continuous metrics; this is not a 38-class classifier.

## Implementation

`sdwan/scenarios.py` is the shared catalogue. The simulator and live netem planner resolve the same cases. New target variants have IDs such as S08-wan2 and S14-wan3-wan1; original S01–S16 meanings remain available through the legacy suite. The raw v1 replay digest at seed80000 matches the pre-change source on both Windows and Linux.

Default initial priority remains WAN1. Target coverage therefore includes standby handling; it does not establish active-path failover on each WAN. Mixed cases keep one healthy alternative. Saturation uses 98% of the target's own capacity and namespace/address, and still requires measured utilization ≥95%. Telemetry markers explicitly name the affected WAN and reject unknown scopes.

Both owned flexiEdge probe helpers were updated after hostname/management NIC/SSH identity verification and old source-hash verification. Each prior helper was backed up. Python3.8 compilation and pure missing/corrupt target fixture checks passed on both guests; no live fault was injected by this task. [Deployment evidence](probe-balanced-deployment-20261002.json).

Runner metadata records the suite version, selected cases, groups, affected links and catalogue SHA-256. Reports separate groups and CONTROL, use selected case denominators and require declared profiles/repeats before granting case coverage. Partial runs cannot claim a full suite. Synthetic output refuses to overwrite an existing evaluation directory. Native controller/policy behavior, SLA thresholds and frozen model weights were preserved.

## Validation and limits

- Windows: **175 unit/behavior tests passed**, excluding the unchanged database integration file.
- Linux: **183 tests passed**, including actual TimescaleDB integration; no skips/failures.
- Ruff check/format passed. PowerShell wrapper syntax was checked separately.
- Balanced simulation smoke: **570 controller trials** =38 cases×3 profiles×5 strategies×1 seed80000. Guarded AI and reactive each meet the gates on 37/37 faults in this seed; priority meets 28/37. This smoke checks the expanded pipeline and does not establish an AI advantage or production reliability.
- The new diagram/report were inspected; case denominators and confidence intervals derive from the actual run.
- Model fingerprint equals the frozen legacy model fingerprint. No retraining or threshold tuning occurred.
- Existing live API/Grafana remained healthy after helper deployment: Manual, configured priorityWAN2, all three WANs fresh, live ML disabled.

New packet experiments were not run. Legacy packet evidence, including S07/S12 failures, remains frozen. The full balanced live grid is38×3profiles×4strategies×3repeats=**1,368 trials /64.6hours minimum**, before startup/cleanup. Start with the availability group/voice/reactive/one repeat (8trials/22.7minutes) after addressing existing failures and reviewing the plan.

Evidence: [verification](scenario-design-20261002-verification.json), [catalogue](scenario-catalogue-balanced-20261002.json), [Windows tests](tests-balanced-unit-windows-20261002.xml), [Linux tests](tests-balanced-full-linux-20261002.xml), [synthetic smoke](../synthetic-balanced-smoke-20261002/EVALUATION.md). Protocol: [EVALUATION_PROTOCOL](../../docs/EVALUATION_PROTOCOL.md).

## Commands

```powershell
.\.venv\Scripts\python.exe -m sdwan scenarios --suite balanced
.\.venv\Scripts\python.exe -m lab.run_live_eval --suite balanced --profiles voice --strategies reactive --repetitions 1 --plan
```

The legacy live default retains16faults; the legacy synthetic suite includes CONTROL as before. Keep v1/v2 results and synthetic/live evidence separate.

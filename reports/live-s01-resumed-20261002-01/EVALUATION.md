# Live flexiWAN packet evaluation

Measured UDP RTT and RTT standard deviation are proxies, not one-way latency/media jitter.

Evidence level: real_packets

Packet measurements completed: 12

Invalid harness trials remain visible and do not count as successful routing.

Native QBR evaluated: True

| Strategy | Cases observed passing | SLA restoration cases | All 16 × 3 profiles evaluated |
|---|---:|---:|---|
| native_priority | 1 | 1 | False |
| native_qbr | 1 | 1 | False |
| predictive_synthetic_lab | 1 | 1 | False |
| reactive | 1 | 1 | False |

Full per-profile pass rates and Wilson intervals are in summary.json/summary.csv.
These results must not be combined with reports/latest (synthetic benchmark).

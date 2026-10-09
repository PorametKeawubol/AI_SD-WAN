# flexiWAN integration status — 2026-10-02 (Asia/Bangkok)

The Windows restart is verified: **VirtualMachinePlatform/WSL Enabled, HypervisorPresent=true, no servicing reboot pending**. `EnablePending` is resolved. The dedicated Ubuntu 24.04 WSL distribution `AI-SDWAN-Lab` runs flexiManage **6.3.37** and two KVM flexiEdges **6.3.35**, with three labeled IPsec tunnels and actual UDP forwarding.

**Current status rechecked at 13:32 Bangkok on 2026-10-02:** HypervisorPresent=true;
the stopped WSL lab was resumed with its original controller data and both VMs.
Both edges are approved/connected, agent policy readback passes, no unresolved
intent remains, and all three tunnels have adjacent peers with 1/4s OSPF timers.
Dashboard HTTP page/health/state/assets return 200 from Windows, with three fresh
healthy WANs, Manual mode, WAN2 priority and ML disabled; LAN ping succeeds 3/3.
See [current verification](status-resume-20261002-final-verification.json) and
[current HTTP readback](status-resume-20261002-final-dashboard-http.json).
Resume now uses a dedicated native Docker socket to prevent Docker Desktop
endpoint takeover. A 0.13s guest clock lead was repaired with the new explicit
`--force-sync` option. Guest NTP caused recurring subsecond leads, so the collector
now waits at most one second for host time to catch up before applying the same
strict timestamp checks. It retains raw timestamps; stale data, larger future
leads and backward clock steps remain rejected. Fifty-nine targeted resume,
clock, collector, service and sampling tests pass on Windows and Linux, with
Ruff check/format passing for seven changed Python files.
All 60 consecutive one-second Windows HTTP observations after the collector fix
had three fresh healthy WANs; see [continuous observation](status-resume-20261002-dashboard-monitor-after-fix.json).
The earlier monitor with intermittent stale data remains preserved in
[pre-fix observations](status-resume-20261002-dashboard-monitor.json).
This status check did not run new experiments.

**The latest S01 voice comparison passed all 12 actual packet trials: four strategies, three repeats each, recovery in 2–4 seconds.** Each strategy used the same revised OSPF timers and matched UDP source ports. Acceptance thresholds and frozen model weights were unchanged.

Reactive voice trials attempted all sixteen cases; **16/16 have valid observations and 14/16 have at least one passing observation**. Valid failures: **S07, S12**. S07 first observed `no_path` after 10.00s, above the 5s gate. S12 achieved 97.14% fault-period SLA, below 98%, despite recovery in 12s. Two invalid initial voice attempts were repeated in a separate output, with both originals preserved. These are exploratory observations, rather than an all-profile reliability result.

Across original and continued experiments: **45 attempts, 43 completed packet trials, 40 valid, 33 passed; 5 invalid harness/setup attempts receive no success credit.** See [CONTINUATION](CONTINUATION.md), [machine-readable results](continuation-summary.json) and [final verification](continuation-verification.json). The original first five trials remain in [LIVE_BRINGUP](LIVE_BRINGUP.md).

| Evidence | Status | Artifact |
|---|---|---|
| Windows features and hypervisor | Enabled / running | [Current readiness](wsl2-readiness.json) |
| Two KVM edges and pinned cloud image | Actual forwarding; image SHA-256 verified | [Image evidence](vm-image-evidence.json), [final readback](continuation-verification.json) |
| Pinned Linux controller, verified HTTPS/schema and enrollment | Used by both edges | [Linux bootstrap](linux-controller-verification.json) |
| Three IPsec tunnels, agent policy and real probes | Both endpoints configured; three healthy probes at completion | [Provisioning](edge-provisioning.json), [final verification](continuation-verification.json) |
| Repeated native priority/QBR/reactive/experimental comparison | S01: 3/3 valid passes per strategy | [Actual comparison](../live-s01-resumed-20261002-01/results.json) |
| Reactive voice coverage | All sixteen attempted; failures/invalids retained | [Case table](CONTINUATION.md) |
| Frozen forecast against recorded probes | Post-hoc t+30s diagnostic; no training or promotion | [Diagnostic](frozen-forecast-continuation-20261002-01.json) |
| Native Windows controller API history | Original API checks preserved | [API evidence](api-verification.json), [original bootstrap](controller-verification.json) |
| Earlier synthetic benchmark: 7,650 trials | Earlier 16/16 result remains synthetic | [Synthetic report](../latest/EVALUATION.md) |

The suite passes **132 tests on Windows and Linux**, with Ruff check/format passing. [Windows JUnit](tests-live-continuation.xml) and [Linux JUnit](linux-tests-live-continuation.xml) retain the runs. Eight targeted analyzer tests passed after the final result metadata changes. Fixture/loopback tests verify software behavior; actual UDP/PCAP trials provide separate forwarding evidence.

The dashboard at **http://127.0.0.1:8000** is running again: backend `flexiwan`, Manual/reactive, configured priority **WAN2**, three fresh healthy WAN measurements, **model_loaded=false**. Final LAN ping succeeded 3/3. The evaluation section labels earlier simulation results as synthetic. [Windows HTTP readback](continuation-dashboard-http.json) verifies page/health/state/assets; interactive browser QA is not claimed. [Integrity evidence](continuation-integrity.json) verifies twenty frozen source files, five model JSON files and all 132 exported captures against their recorded hashes.

Original tunnel OSPF hello/dead timers were 10/40s. The revised lab uses 1/4s on all three tunnels of both edges. Source ports and experimental causal resampling also changed, so the comparison is an engineering revision rather than an isolated causal estimate of timer tuning. Original QBR repeats with zero-second recovery already used backups before WAN1 was cut, as shown by [capture directions](../live-s01-repeats-20261002-01/path-analysis.json).

Windows suspend interrupted the first revised batch and displaced guest clocks by several hours. Its packet underdelivery and subsequent preflight failure remain invalid. [Windows events](host-suspend-evidence-20261002.json), [guest clock repair](clocks-after-host-resume-20261002-01.json) and original attempts are retained. The Windows experiment wrapper requests system activity temporarily; the runner stops on clock discontinuities or long scheduling gaps. Explicit sleep/lid actions can still interrupt a run.

This nested AMD lab uses OpenSSL crypto because the pinned native VPP engine aborted during IPsec SA creation. Probes use verified VPP Linux TAPs and VPP counters. A cold-ARP full-loss probe can exceed its measurement window; incomplete observations stay missing and are never healthy zeros. Encrypted PCAP frame counts corroborate paths; UDP echoes establish delivery. See the [runbook](../../docs/REAL_TESTBED.md).

Linux project: `/opt/ai-sdwan-lab`. Controller HTTPS: `192.168.56.1:3443`. Mongo/Redis are internal; Mailpit is local on `127.0.0.1:8025`. Docker Desktop's existing projects and the original proposal remain preserved. Secrets/private logs remain in ignored runtime directories; public exports are scanned for credentials/private keys.

For future boots follow [RESTART_RESUME](RESTART_RESUME.md): resume verified existing resources, wait for edges, check clocks/timers, then start the dashboard. Resume checks network identity, VM disks/NICs and namespace peers; hidden keepalive keeps WSL active. Resume has now been verified from a stopped WSL distribution; a further Windows cold reboot after VM creation has not been performed.

The full **576-trial** smoke grid, all three application profiles across every strategy, and independent real-data model training/validation/held-out evaluation remain incomplete. The grid alone requires at least 27.2 hours plus setup/cleanup. Synthetic ML remains disabled in the normal service; the two current voice case failures are not waived.

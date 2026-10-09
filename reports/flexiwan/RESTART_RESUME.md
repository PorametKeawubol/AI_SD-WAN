# Resume the existing lab after Windows restarts

Feature activation is complete, rechecked on 2026-10-02: VirtualMachinePlatform
and WSL Enabled, HypervisorPresent=true, no servicing reboot pending. The Linux
controller and two KVM edges forward actual traffic through three IPsec tunnels.
See [STATUS](STATUS.md), [continued trials](CONTINUATION.md) and
[original bring-up](LIVE_BRINGUP.md).


Current stack update, 2026-10-02: resume also starts the existing TimescaleDB/Grafana compose using the explicit native Docker socket. Grafana is at <https://localhost:3000/d/ai-sdwan>; the secure control API is at <https://localhost:8000/docs>. Login/token files are in ignored Windows `lab/runtime/observability/`. The Windows CurrentUser CA store trusts the lab CA. See [stack migration](STACK_MIGRATION.md) and [HTTPS/SQL/agent readback](stack-migration-20261002-verification.json). Older HTTP snapshots below describe the pre-migration service.

Workspace: `E:\Proposal\AI_SD-WAN`.
Distro: `AI-SDWAN-Lab`. Linux project: `/opt/ai-sdwan-lab`.

After a future Windows reboot, run in PowerShell:

```powershell
Set-Location E:\Proposal\AI_SD-WAN
.\scripts\check-wsl2.ps1
.\scripts\start-wsl-lab.ps1
# Allow both existing edges to reconnect; policy services must be stopped.
$resumeStamp = Get-Date -Format yyyyMMdd-HHmmss
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/sync_edge_clocks.py --apply --force-sync --output "reports/flexiwan/resume-$resumeStamp-clock.json"
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/configure_lab_ospf.py --apply --output "reports/flexiwan/resume-$resumeStamp-ospf.json"
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_live_lab.py --background
Invoke-RestMethod https://localhost:8000/health
```

Expected health: `backend=flexiwan`, `storage=timescaledb`, `model_loaded=false`. Manual is the default; inspect the persisted mode after restarting. Resume starts existing verified resources; it does not create
credentials, reinstall edges or overwrite disks. Hidden keepalive prevents WSL
stopping after the command shell exits. Partial namespaces/stale bridges are
preserved for inspection. On 2026-10-02, resume was verified from a stopped WSL
distribution: the original controller data and both existing VMs were restored.
A further Windows cold reboot after VM creation has not been tested.

Docker Desktop redirected the default `/run/docker.sock` to its own Engine.
`start-wsl-lab.ps1` now configures and verifies the native lab Engine 29.1.3 at
`unix:///run/ai-sdwan-docker.sock`; `lab.resume` selects that endpoint explicitly.
The socket override applies only to this owned distribution. For manual Docker
commands inside it, use `export DOCKER_HOST=unix:///run/ai-sdwan-docker.sock`.
The original database, image, enrollment and policy were reused.
See [resume verification](status-resume-20261002-final-verification.json) and
[Windows HTTP readback](status-resume-20261002-final-dashboard-http.json).

The clock helper verifies each SSH identity/hostname/management MAC and repairs
only guest realtime when necessary. Tunnel timer tuning uses the original
per-guest backup, applies hello/dead 1/4 seconds on the three known tunnels, and
waits for all adjacencies on both edges. Verify again after controller/tunnel
reconfiguration; this is a lab FRR override. Both helpers require fresh evidence
files and refuse mutations while a policy service is listening. `--force-sync`
also repairs subsecond skew: the observed 0.13s lead caused strict future-sample
rejection despite passing the previous one-second clock guard. Telemetry's
freshness and future-timestamp checks remain unchanged. Because guest NTP can
restore a small lead, the collector waits at most one second for host time to
reach the raw sample timestamp, then validates normally. It never rewrites the
timestamp, accepts a future value, or waits for a lead larger than one second.

Before a packet experiment, stop the decision API; Grafana can keep reading:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_live_lab.py --stop
```

Run from Windows with a fresh output name:

```powershell
.\scripts\run-wsl-experiment.ps1 -OutputName live-s01-new -Repetitions 3
```

The wrapper temporarily prevents idle system sleep and releases its request
when the command exits. The helper verifies guest clocks/routing; the runner
invalidates and stops trials after clock discontinuities or long scheduling
gaps. Explicit sleep/lid actions can still interrupt the run. A previous Windows
suspend and its invalid trials remain in the [continued report](CONTINUATION.md).

Use the [runbook](../../docs/REAL_TESTBED.md) for other cases/profiles.
Runner refuses occupied decision API/experiment ports. Keep original failed
trials and do not delete unresolved journals to bypass controller uncertainty.
The full 576-trial benchmark has not run and requires at least 27.2 hours.
Model performance on real data remains unvalidated.

Native Windows state remains in `.work/native/`; this edge lab uses its own
Linux controller and private `lab/runtime/credentials.json`. Post-migration software validation: 146 tests pass on Windows and Linux, including actual TimescaleDB integration. The latest S01 comparison has
12 valid, passing packet trials; earlier failures remain in the report.
Reactive voice coverage now has valid observations in all sixteen cases and
passing observations in fourteen. S07 no_path detection and S12 gradual
degradation still fail their unchanged gates. This is not the full 576-trial
benchmark or real-data model validation.

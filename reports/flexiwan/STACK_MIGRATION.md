# Requested stack migration — 2026-10-02

The requested flexiWAN / FastAPI / Python / TimescaleDB / Grafana / XGBoost stack is implemented and running in the existing owned `AI-SDWAN-Lab`. The controller and edge versions, VM identities, policy, frozen model files and historical packet experiments were preserved.

## Running stack

| Component | Implementation |
|---|---|
| SD-WAN platform / edges / controller | flexiWAN; two flexiEdge 6.3.35 KVM guests; flexiManage 6.3.37 |
| Telemetry and decisions | FastAPI 0.142.2, Python, one live decision worker |
| Database | TimescaleDB 2.30.2, PostgreSQL 17, real telemetry hypertable |
| Monitoring | Grafana 13.2.3, 15 provisioned panels, read-only SQL role |
| ML | XGBoost 3.4.1; frozen synthetic model retained; normal live inference disabled pending real-data validation |
| Controller / database communication | REST API over verified HTTPS; parameterized SQL through psycopg 3.3.6 |
| Security | API Bearer token, native API/Grafana HTTPS, PostgreSQL TLS verify-full |

TimescaleDB and Grafana use separate compose project `ai-sdwan-observability` on native Engine 29.1.3 at `unix:///run/ai-sdwan-docker.sock`. Images are pinned by registry digest in [monitoring-lock.json](../../lab/monitoring-lock.json):

- `timescale/timescaledb:2.30.2-pg17@sha256:b346edcdb51a1fd6020e3965e0bd1c9f3406fa6d5fbce1e28f4852587ef934e2`
- `grafana/grafana:13.2.3@sha256:b28bae15e219c998fb0e0424ed724930cc61b1f61fb404d47c862f9a23f9e572`

PostgreSQL, Grafana and the API bind to loopback. Grafana's own metadata uses PostgreSQL. The Grafana datasource uses `sdwan_reader`, whose actual permissions allow SELECT and deny INSERT. MongoDB and Redis remain upstream flexiManage dependencies; Mailpit handles local bootstrap mail.

## Data preservation and cleanup

Imported **6,384 telemetry records, 2,287 events and 2 state records** into the empty `live` stream. Raw UNIX timestamps, JSON payloads, event IDs, histories and saved mode/profile/path were preserved. Source and imported canonical content SHA-256 match:

`ebc2bb8b6018d0cdc758bfe550f64b0a1d89414e5cda7bceeafe29427d84a61a`

The source SQLite file SHA-256 is `01abb73173143ca82cb8e694a4b8cfece005572de4a42a7586f86a2248cdb400`. [Migration evidence](stack-migration-20261002-data.json) records the actual counts and hashes. New live samples continue entering the Timescale hypertable; the SQL readback already saw 7,140 rows.

The SQLite production backend, the custom monitoring files `sdwan/static/index.html`, `app.js`, `style.css`, and the obsolete HTTP/custom-dashboard verifier were removed. The old Linux operational database/WAL/SHM were archived under ignored `lab/runtime/observability/legacy-state/`; the old Windows simulation database was archived under `.work/stack-before-timescale-grafana-20261002/legacy-data/`. Both archives have matching SHA-256. Source files have a separate `.work/stack-before-timescale-grafana-20261002/` backup.

Historical `reports/**/audit.sqlite3` artifacts remain frozen evidence. SQLite is only used by the one-way read-only legacy importer and its fixtures; runtime storage has no SQLite fallback. Future experiments have unique TimescaleDB streams and export `audit.json` plus `database-stream.json`.

Archive evidence: [Linux](stack-migration-20261002-legacy-archive.json), [Windows](stack-migration-20261002-windows-archive.json).

## Verification

| Check | Observed result |
|---|---|
| Windows full tests, including actual SQL integration | 146 passed, no skips, 239.795 seconds |
| Linux final full tests, including actual SQL integration | 146 passed, no skips, 8.981 seconds |
| Windows targeted API/service tests after local Swagger changes | 15 passed |
| Ruff | check and format check passed for sdwan/lab/scripts/tests |
| Telemetry migration | exact canonical content SHA-256 match |
| TimescaleDB | telemetry is an actual hypertable; SQL connection uses TLSv1.3 |
| Grafana | healthy PostgreSQL metadata, datasource connection OK; all 14 SQL panels and stream selector execute against real data |
| Grafana/API page assets | Grafana HTML and 7 JS/CSS assets; Swagger HTML/JS/CSS/favicon return HTTPS 200 |
| API protections | missing token 401, cross-origin 403, synthetic injection on real backend 403; obsolete assets 404 |
| Windows HTTPS | default Windows trusted CA store verifies API/Grafana; no TLS verification disabled |
| Existing-resource resume | `lab.resume --apply` repeated successfully; both VMs/networks already running; monitoring compose reused |
| Post-resume live collection | seven checks over 60 seconds: all three WANs up/fresh, timestamps advancing, Manual and ML off |
| flexiWAN readback | both approved/connected agents match controller policy; 3 tunnels configured; 0 unresolved intents |
| LAN packet readback | 3 sent / 3 received through existing lab |

Tests report one existing Starlette/httpx deprecation warning. Unit policy/API fixtures use an in-memory test double; SQL integration tests exercise the actual TimescaleDB with isolated test streams that are cleaned up afterward.

Evidence: [Linux HTTPS/SQL/controller/agent/LAN](stack-migration-20261002-verification.json), [Windows HTTPS and 60-second freshness](stack-migration-20261002-windows-https.json), [Windows JUnit](windows-tests-stack-timescale-grafana.xml), [Linux JUnit](linux-tests-stack-timescale-grafana.xml).

Interactive visual QA remains unverified: the native UI bridge is unavailable and the tool browser returns ERR_CERT_AUTHORITY_INVALID for the lab CA. No browser security warning was bypassed. HTTP assets and actual Grafana query execution were verified separately.

## Access and restart

- Grafana: <https://localhost:3000/d/ai-sdwan>
- Control API and approvals: <https://localhost:8000/docs>
- Health: <https://localhost:8000/health>

Windows login details: ignored `lab/runtime/observability/grafana-login.json`; API token: `lab/runtime/observability/api-token.txt`. Database passwords/TLS private keys remain in Linux runtime. The lab CA is trusted in Windows CurrentUser Root. Use Swagger **Authorize** for API operations; tokens are not persisted in browser storage.

Follow [RESTART_RESUME.md](RESTART_RESUME.md) after reboot. Resume starts the existing monitoring compose automatically; the live API helper loads database/TLS/API/controller credentials privately. Stop only the live decision API before packet experiments; Grafana can keep reading. Read-only verification is reproducible with `scripts/verify_stack.py --output <new-report.json>` inside the owned Linux lab.

Migration did not rerun the frozen packet benchmark, change SLA gates, or validate/promote ML. The previous S07/S12 failures, incomplete 576-trial grid and need for real-data ML validation remain documented in [CONTINUATION.md](CONTINUATION.md).

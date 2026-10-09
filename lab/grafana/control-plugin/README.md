# Grafana Control and approvals

This local Grafana app contains a panel and three HTTPS proxy routes. It uses
Grafana session authentication: Viewer can read state, and only an organization
Admin can change configuration or review a pending recommendation. The server
injects the existing API token from encrypted `secureJsonData`; no token is sent
to panel JavaScript, dashboard JSON, or the repository.

The panel polls current API state every five seconds. It disables writes for an
archived stream, expired recommendation, failed state refresh, inflight apply,
or native QBR observation. The decision API still validates the pending ID,
expiry and current network state, and verifies controller/agent readback.

Build and verify after source changes:

```powershell
cd lab/grafana/control-plugin
npm ci --ignore-scripts
npm run build
npm test
```

The small AMD modules use React and Grafana modules provided by Grafana itself.
The built `dist/` is committed so starting the lab needs no Node.js installation.
Compose allows these two specific local unsigned plugin IDs, keeps HTML
sanitization enabled, and retains TLS verification. In the native Linux lab,
Grafana uses host networking with `http_addr=127.0.0.1` to reach the API on
loopback. Its TimescaleDB hostname maps to the existing loopback database port.

Run `scripts/prepare_observability.py` in the owned Linux lab before starting
Compose. On an existing installation it preserves all credentials/certificates
and provisions the current API token into the ignored private Grafana env file.
Recreate only the Grafana service to load this plugin and its provisioning.

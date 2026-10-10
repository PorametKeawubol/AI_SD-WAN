# Community flexiManage GUI trial

The lab now serves the React frontend from
[Aikonlee/fleximanage](https://github.com/Aikonlee/fleximanage), pinned at
`1bcaeec0491acb4e70809ee098545f9912d14765`. This is a community-written GUI named
**OpenSource-OpenNetworking**, separate from the original flexiWAN vendor GUI.
The source license is AGPLv3.

- URL: <https://localhost:18443/devices>
- Tunnels: <https://localhost:18443/tunnels>
- Login: ignored `lab/runtime/community-gui-login.json` on Windows.
- Mode: **read-only trial**. Login uses the existing isolated lab account;
  configuration-changing requests are blocked by the local server, regardless of
  the account's original permissions.
- Matching modified frontend source: <https://localhost:18443/trial/source.zip>.

The trial uses the existing flexiManage **6.3.37** controller and flexiEdge
**6.3.35** guests. It does not install the fork's backend, migrate devices, change
database schemas, or replace the Python decision service. Grafana remains the
interface for AI/ML forecasts and Auto/Manual/approval controls.

## Verified views and limitations

Devices and Tunnels read authenticated APIs of the real local controller. The
initial test found two approved, connected, running devices and three connected
tunnels, with actual reported RTT and drop-rate measurements.

Small display fixes adapt the community frontend to the pinned controller:

- Router state uses the actual nested `state.state` field.
- Home counts use the actual devices/tunnels response fields; unavailable values
  remain unavailable instead of becoming healthy zeroes.
- Tunnel metrics read `tunnelStatusA/B.rtt` and `drop_rate`, and connected counts
  use the reported connection status rather than configuration alone.
- Interfaces do not infer an UP link solely from a configured IP address.
- Device traffic uses real `state.ifStats` counters. In this controller version,
  the fields named `*_bps` hold bytes/second; the GUI converts them to Mbps with
  `bytes_per_second * 8 / 1e6`. Missing counters display `N/A`.
- Home and Devices poll every 30 seconds; Tunnels retains its own 30-second refresh.
- Authentication preserves the controller's real `Refresh-JWT` and
  `refresh-token` headers. Credential/token debug logging is removed.

The original controller's `/organizations/summary` returns HTTP 500 in this lab.
Home falls back to real device/tunnel reads and explicitly marks monthly traffic
unavailable. The community map-tile endpoint is absent in the original backend,
so map backgrounds are unavailable. Other menus have not been validated as a
complete replacement for the vendor application. Buttons that change network
configuration receive a read-only error in this trial.

## Start and stop

The frontend build is already prepared in the ignored vendor directory. To
reopen the trial with the lab running:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_community_gui.py --background
```

To stop only this GUI trial:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_community_gui.py --stop
```

The server binds to loopback port 18443. Browser HTTPS uses the lab's existing
trusted certificate; upstream controller HTTPS validates its existing CA.
Explicit read-route and origin checks protect this trial. The server does not
forward all GET routes because some upstream MFA GET endpoints alter data.
Obsolete upstream `direct-login.html` and `fix-auth.html` debug pages are not
served; only the normal application and its build assets are available.

## Reproduce the frontend

Source pin/build hashes are recorded in `lab/community-gui-lock.json`. Local
frontend changes are recorded in `lab/community-gui.patch`.

```powershell
git clone https://github.com/Aikonlee/fleximanage.git vendor/community-fleximanage
git -C vendor/community-fleximanage checkout 1bcaeec0491acb4e70809ee098545f9912d14765
git -C vendor/community-fleximanage apply ../../lab/community-gui.patch
Set-Location vendor/community-fleximanage/frontend
npm.cmd ci --ignore-scripts --no-audit --no-fund
npm.cmd run build
```

Copy the resulting build and matching frontend source archive into the owned
Linux lab before starting the helper. Preserve the original controller/edge
versions and validate any future write capability separately from this trial.

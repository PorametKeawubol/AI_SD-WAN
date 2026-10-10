# Official flexiWAN GUI access

The lab exposes the **vendor-packaged flexiEdge WebUI 1.10.9**, included in
flexiwan-router 6.3.35. No custom GUI was built.

| Device | Windows URL |
| --- | --- |
| sdw-edge-a | <http://localhost:18081/> |
| sdw-edge-b | <http://localhost:18082/> |

Log in as `flexiui`. The generated password is stored locally in ignored
`lab/runtime/official-gui-login.json` on Windows and in the owned WSL lab.
The dedicated Linux accounts have a noninteractive shell; the existing `lab`
and `root` password states are unchanged. Both GUI logins and authenticated
device-status reads were verified on 10 October 2026, with both vRouters running.

The GUI services are enabled on both guests. Windows access uses loopback SSH
relays with the existing verified lab SSH host keys. `scripts/start-wsl-lab.ps1`
restores the relays when the GUI credential file exists. To reopen only these
relays while the lab is already running:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/expose_official_edge_ui.py
```

flexiEdge WebUI provides local device setup and troubleshooting. It is separate
from the central **flexiManage GUI** that manages devices, tunnels, and policies.
The public flexiManage repository excludes the private SaaS `client` submodule.
The genuine central GUI requires a verified vendor Cloud account or a
vendor-supplied commercial self-host package. No Cloud account was created and
the existing edges remain registered to the local controller.

Sources: [flexiEdge WebUI documentation](https://docs.flexiwan.com/flexiEdgeUI/overview.html),
[flexiManage repository](https://gitlab.com/flexiwangroup/fleximanage/-/tree/dev),
[vendor hosting options](https://flexiwan.com/faq/).

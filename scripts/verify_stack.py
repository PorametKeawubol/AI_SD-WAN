"""Read back the owned live stack; never apply a policy or expose credentials."""

import argparse
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from sdwan.adapters import FlexiWANAdapter
from sdwan.flexiwan import FlexiWANConfig

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify():
    require(os.name == "posix" and (ROOT / ".ai-sdwan-owned").is_file(), "Use the owned Linux lab")
    private = json.loads((ROOT / "lab/runtime/observability/private.json").read_text())
    credentials = json.loads((ROOT / "lab/runtime/credentials.json").read_text())
    context = ssl.create_default_context(cafile=str(ROOT / "lab/runtime/certs/certificate.pem"))
    result = {
        "observed_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "evidence_level": "actual_https_sql_controller_agent_and_lan_packet_readback",
        "new_experiments_run": False,
        "model_validated_on_real_data": False,
    }
    with httpx.Client(verify=context, trust_env=False, timeout=20) as client:
        api = "https://localhost:8000"
        token = {"Authorization": "Bearer " + private["api_token"]}
        health = client.get(api + "/health")
        health.raise_for_status()
        require(health.json()["storage"] == "timescaledb", "API is not using TimescaleDB")
        require(client.get(api + "/api/state").status_code == 401, "API authentication missing")
        require(
            client.get(
                api + "/api/state", headers={**token, "Origin": "https://example.org"}
            ).status_code
            == 403,
            "Cross-origin API access allowed",
        )
        injection = client.post(api + "/api/telemetry", headers=token, json={"samples": [{}]})
        require(injection.status_code == 403, "Live API allowed synthetic telemetry injection")
        require(client.get(api + "/app.js").status_code == 404, "Old dashboard still served")
        require(client.get(api + "/style.css").status_code == 404, "Old dashboard CSS still served")
        redirect = client.get(api + "/")
        require(
            redirect.status_code == 307
            and redirect.headers["location"] == "https://localhost:3000/d/ai-sdwan",
            "API root does not link to Grafana",
        )
        docs = client.get(api + "/docs")
        docs.raise_for_status()
        require(
            'nonce="' in docs.text and "nonce-" in docs.headers["content-security-policy"],
            "Docs CSP invalid",
        )
        for asset in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
            client.get(api + "/openapi-assets/" + asset).raise_for_status()
        schema = client.get(api + "/openapi.json").json()
        require(
            schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer",
            "Docs authorization absent",
        )
        state = client.get(api + "/api/state", headers=token).json()
        require(
            state["mode"] == "manual" and not state["model_loaded"],
            "Live mode or ML activation changed",
        )
        require(
            set(state["telemetry"]) == {"wan1", "wan2", "wan3"}
            and all(row["up"] and row["fresh"] for row in state["telemetry"].values()),
            "Three fresh measured WANs required",
        )
        result["decision_api"] = {
            "url": api,
            "health": health.json(),
            "mode": state["mode"],
            "configured_priority": state["confirmed_path"],
            "fresh_wans": state["telemetry"],
            "bearer_required": True,
            "cross_origin_rejected": True,
            "synthetic_injection_rejected": True,
            "local_swagger_assets_verified": True,
            "old_dashboard_routes_removed": True,
        }

    with httpx.Client(
        base_url="https://localhost:3000",
        verify=context,
        trust_env=False,
        auth=(private["grafana_user"], private["grafana_password"]),
        timeout=30,
    ) as grafana:
        health = grafana.get("/api/health")
        health.raise_for_status()
        require(health.json()["database"] == "ok", "Grafana metadata database unhealthy")
        settings = grafana.get("/api/admin/settings")
        settings.raise_for_status()
        require(
            settings.json()["database"]["type"] == "postgres", "Grafana metadata is not PostgreSQL"
        )
        source = grafana.get("/api/datasources/uid/sdwan-timescale")
        source.raise_for_status()
        source_config = source.json()["jsonData"]
        require(
            source_config["timescaledb"] and source_config["sslmode"] == "verify-full",
            "Datasource TLS incorrect",
        )
        source_health = grafana.get("/api/datasources/uid/sdwan-timescale/health")
        source_health.raise_for_status()
        dashboard = grafana.get("/api/dashboards/uid/ai-sdwan").json()["dashboard"]
        queries = []
        panel_titles = {}
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                reference = str(panel["id"])
                queries.append(
                    {
                        **target,
                        "refId": reference,
                        "rawSql": target["rawSql"].replace("${stream:sqlstring}", "'live'"),
                        "intervalMs": 5000,
                        "maxDataPoints": 720,
                    }
                )
                panel_titles[reference] = panel["title"]
        queries += [
            {
                "refId": "streams",
                "datasource": {"type": "grafana-postgresql-datasource", "uid": "sdwan-timescale"},
                "rawSql": "SELECT DISTINCT stream FROM telemetry ORDER BY stream",
                "format": "table",
                "rawQuery": True,
            },
            {
                "refId": "permissions",
                "datasource": {"type": "grafana-postgresql-datasource", "uid": "sdwan-timescale"},
                "rawSql": "SELECT current_user AS role, has_table_privilege(current_user,'telemetry','SELECT') AS readable, has_table_privilege(current_user,'telemetry','INSERT') AS writable",
                "format": "table",
                "rawQuery": True,
            },
        ]
        now = time.time()
        response = grafana.post(
            "/api/ds/query",
            json={
                "from": str(int((now - 3600) * 1000)),
                "to": str(int(now * 1000)),
                "queries": queries,
            },
        )
        response.raise_for_status()
        query_results = response.json()["results"]
        summaries = []
        for reference, title in panel_titles.items():
            body = query_results[reference]
            require(
                body.get("status", 200) == 200 and not body.get("error"),
                "Grafana query failed: " + title,
            )
            frames = body.get("frames", [])
            rows = sum(
                len(frame["data"]["values"][0]) if frame["data"]["values"] else 0
                for frame in frames
            )
            require(rows > 0, "Grafana panel returned no data: " + title)
            if reference in {"1", "2", "3"}:
                require(frames[0]["data"]["values"][0] == [1], "Grafana WAN status is not UP")
            summaries.append({"panel": title, "frames": len(frames), "rows": rows, "status": "ok"})
        permissions = query_results["permissions"]["frames"][0]["data"]["values"]
        require(
            permissions == [["sdwan_reader"], [True], [False]], "Grafana datasource is writable"
        )
        require(not query_results["streams"].get("error"), "Grafana stream selector failed")
        html = grafana.get("/", follow_redirects=True)
        html.raise_for_status()
        assets = re.findall(r'(?:src|href)="(public/[^"?]+\.(?:js|css))', html.text)
        require(assets, "Grafana page assets absent")
        for asset in assets:
            grafana.get("/" + asset).raise_for_status()
        result["grafana"] = {
            "url": "https://localhost:3000/d/ai-sdwan",
            "version": health.json()["version"],
            "metadata_database": "postgres",
            "datasource_health": source_health.json()["message"],
            "datasource_role": "sdwan_reader",
            "datasource_read_only": True,
            "database_tls_mode": "verify-full",
            "panels_provisioned": len(dashboard["panels"]),
            "sql_panels_verified": summaries,
            "stream_selector_verified": True,
            "html_and_assets_verified": len(assets),
        }

    with psycopg.connect(private["database_url"], row_factory=dict_row) as connection:
        result["timescaledb"] = {
            "version": connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"
            ).fetchone()["extversion"],
            "hypertables": connection.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables"
            ).fetchall(),
            "tls": connection.execute(
                "SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone(),
            "live_telemetry_rows": connection.execute(
                "SELECT count(*) AS n FROM telemetry WHERE stream='live'"
            ).fetchone()["n"],
        }
        require(result["timescaledb"]["tls"]["ssl"], "Database transport is unencrypted")

    os.environ["FLEXIWAN_ACCESS_KEY"] = credentials["access_key"]
    adapter = FlexiWANAdapter(FlexiWANConfig.load(ROOT / "config/flexiwan.local.json"))
    try:
        policy, devices = adapter.client.preflight()
        adapter.client.readback(policy)
        require(not adapter.journal.unresolved(), "Unresolved policy intent remains")
        tunnels = adapter.client.request("GET", "tunnels")
        require(
            len(tunnels) == 3
            and all(
                not row.get("isPending") and row.get("deviceAconf") and row.get("deviceBconf")
                for row in tunnels
            ),
            "Tunnel configuration incomplete",
        )
        result["flexiwan"] = {
            "controller_version": adapter.client.verify_version()["version"],
            "devices": [
                {key: row.get(key) for key in ("hostname", "isApproved", "isConnected")}
                for row in devices
            ],
            "agent_policy_readback": True,
            "unresolved_policy_intents": 0,
            "installed_tunnels": 3,
        }
    finally:
        adapter.client.close()
    ping = subprocess.run(
        ["ip", "netns", "exec", "sdw-client", "ping", "-n", "-c", "3", "-W", "1", "10.30.0.2"],
        capture_output=True,
        text=True,
        check=True,
        timeout=6,
    )
    require("3 packets transmitted, 3 received" in ping.stdout, "LAN packet readback failed")
    result["lan_ping"] = {"sent": 3, "received": 3}
    result["visual_browser_qa"] = {
        "verified": False,
        "reason": "Native UI bridge unavailable; tool browser does not trust lab CA. No security warning bypassed.",
    }
    data = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    secrets = [
        private[key]
        for key in (
            "postgres_password",
            "sdwan_password",
            "reader_password",
            "grafana_db_password",
            "grafana_password",
            "api_token",
        )
    ]
    secrets += [credentials[key] for key in ("password", "access_key", "enrollment_token")]
    require(
        not any(value.encode() in data for value in secrets) and b"PRIVATE KEY-----" not in data,
        "Private material in public verification",
    )
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), "Preserve existing evidence; choose a new output file")
    data = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print("Verified TimescaleDB TLS, Grafana SQL panels, secure API, both agents, and LAN 3/3.")


if __name__ == "__main__":
    main()

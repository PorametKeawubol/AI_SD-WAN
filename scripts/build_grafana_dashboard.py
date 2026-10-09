"""Build the provisioned Grafana dashboard from actual TimescaleDB SQL queries."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {"type": "grafana-postgresql-datasource", "uid": "sdwan-timescale"}
LATEST_LIVE = "__latest_live__"
LIVE_STREAM = (
    "(SELECT stream FROM state WHERE key='service_status' "
    "AND payload->>'backend' IN ('flexiwan','flexiwan_observe') "
    "ORDER BY updated_at DESC LIMIT 1)"
)
STREAM = (
    f"stream=CASE WHEN ${{stream:sqlstring}}='{LATEST_LIVE}' "
    f"THEN {LIVE_STREAM} ELSE ${{stream:sqlstring}} END"
)
STREAM_QUERY = (
    "SELECT __text,__value FROM ("
    f"SELECT 'Latest live' AS __text,'{LATEST_LIVE}' AS __value,0 AS priority "
    "UNION ALL SELECT DISTINCT stream AS __text,stream AS __value,1 AS priority "
    "FROM telemetry) AS streams ORDER BY priority,__text"
)


def panel(identifier, title, kind, sql, x, y, width, height, *, unit="none", format="table"):
    return {
        "id": identifier,
        "title": title,
        "type": kind,
        "gridPos": {"x": x, "y": y, "w": width, "h": height},
        "datasource": SOURCE,
        "targets": [
            {"refId": "A", "datasource": SOURCE, "rawSql": sql, "format": format, "rawQuery": True}
        ],
        "fieldConfig": {
            "defaults": {"unit": unit, "noValue": "NO DATA", "color": {"mode": "palette-classic"}},
            "overrides": [],
        },
        "options": {
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "/.*/" if kind == "stat" else "",
                "values": False,
            },
            "textMode": "auto",
            "colorMode": "value",
            "graphMode": "none",
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
    }


def build():
    panels = []
    for index, link in enumerate(("wan1", "wan2", "wan3")):
        sql = f"""SELECT CASE WHEN observed_at < now()-INTERVAL '10 seconds' THEN 2 WHEN up THEN 1 ELSE 0 END AS status
FROM telemetry WHERE {STREAM} AND link_id='{link}' ORDER BY observed_at DESC LIMIT 1"""
        item = panel(index + 1, link.upper() + " health", "stat", sql, index * 8, 0, 8, 4)
        item["fieldConfig"]["defaults"]["mappings"] = [
            {
                "type": "value",
                "options": {
                    "0": {"text": "DOWN", "color": "red"},
                    "1": {"text": "UP", "color": "green"},
                    "2": {"text": "STALE", "color": "orange"},
                },
            }
        ]
        panels.append(item)
    for index, (key, title) in enumerate(
        (
            ("confirmed_path", "Configured priority"),
            ("mode", "Control mode"),
            ("profile", "Application profile"),
            ("model_loaded", "ML model active"),
        )
    ):
        sql = (
            f"SELECT payload->>'{key}' AS value FROM state WHERE {STREAM} AND key='service_status'"
        )
        panels.append(panel(4 + index, title, "stat", sql, index * 6, 4, 6, 4))
    for index, (column, title, unit) in enumerate(
        (
            ("latency_ms", "Latency", "ms"),
            ("jitter_ms", "Jitter", "ms"),
            ("loss_pct", "Packet loss", "percent"),
            ("utilization_pct", "WAN utilization", "percent"),
        )
    ):
        sql = f"""SELECT time_bucket('5 seconds', observed_at) AS time, link_id AS metric, avg({column}) AS value
FROM telemetry WHERE {STREAM} AND $__timeFilter(observed_at) GROUP BY 1,2 ORDER BY 1,2"""
        item = panel(
            8 + index,
            title,
            "timeseries",
            sql,
            (index % 2) * 12,
            30 + (index // 2) * 8,
            12,
            8,
            unit=unit,
            format="time_series",
        )
        item["fieldConfig"]["defaults"]["custom"] = {
            "drawStyle": "line",
            "lineInterpolation": "linear",
            "lineWidth": 2,
            "fillOpacity": 8,
            "spanNulls": False,
            "showPoints": "never",
        }
        panels.append(item)
    sql = f"""SELECT observed_at AS time, kind AS event, payload::text AS detail
FROM events WHERE {STREAM} AND $__timeFilter(observed_at) ORDER BY id DESC LIMIT 100"""
    panels.append(panel(12, "Decision and approval audit", "table", sql, 0, 46, 24, 8))
    recommendation = f"SELECT COALESCE(payload->'pending'->>'target','None') AS target FROM state WHERE {STREAM} AND key='service_status'"
    panels.append(panel(13, "Pending recommendation", "stat", recommendation, 16, 8, 8, 4))
    freshness = f"SELECT extract(epoch from now()-updated_at)::double precision AS age FROM state WHERE {STREAM} AND key='service_status'"
    panels.append(panel(14, "Decision service heartbeat", "stat", freshness, 16, 12, 8, 4))
    controls = panel(
        15,
        "Control and approvals",
        "aisdwan-controls-panel",
        f"SELECT {LIVE_STREAM} AS live_stream",
        0,
        8,
        16,
        8,
    )
    controls["options"] = {}
    panels.append(controls)
    environment = (
        "SELECT COALESCE((SELECT CASE "
        "WHEN payload->>'active'='true' AND updated_at > now()-INTERVAL '25 seconds' "
        "THEN 'Natural WAN: Fiber / Broadband / Mobile' "
        "WHEN payload->>'active'='true' THEN 'Natural WAN status stale (check runner)' "
        "ELSE 'Natural WAN stopped' END "
        f"FROM state WHERE {STREAM} AND key='wan_environment'), 'Natural WAN not enabled') AS environment"
    )
    panels.append(panel(16, "WAN environment (lab profiles)", "stat", environment, 0, 16, 24, 3))
    latest_decision = f"""SELECT payload->'last_decision'->>'action' AS action,
upper(payload->'last_decision'->>'source') AS source,
upper(payload->'last_decision'->>'target') AS target,
payload->'last_decision'->>'reason' AS reason,
upper(payload->'apply_inflight'->>'target') AS applying,
extract(epoch from now()-updated_at)::double precision AS age_seconds
FROM state WHERE {STREAM} AND key='service_status'"""
    panels.append(panel(17, "Latest decision and reason", "table", latest_decision, 0, 19, 24, 4))
    forecasts = f"""SELECT upper(wan.key) AS wan,
CASE WHEN now() - to_timestamp((wan.value->>'timestamp')::double precision) > INTERVAL '10 seconds'
THEN 'STALE' WHEN (wan.value->>'up')::boolean THEN 'UP' ELSE 'DOWN' END AS status,
CASE WHEN (wan.value->>'up')::boolean THEN (wan.value->>'latency_ms')::double precision END AS measured_rtt_ms,
(payload->'last_decision'->'predicted'->wan.key->>0)::double precision AS forecast_rtt_30s_ms,
CASE WHEN (wan.value->>'up')::boolean THEN (wan.value->>'jitter_ms')::double precision END AS measured_jitter_ms,
(payload->'last_decision'->'predicted'->wan.key->>1)::double precision AS forecast_jitter_30s_ms,
(wan.value->>'loss_pct')::double precision AS measured_loss_pct,
(payload->'last_decision'->'predicted'->wan.key->>2)::double precision AS forecast_loss_30s_pct,
(wan.value->>'utilization_pct')::double precision AS measured_utilization_pct,
(payload->'last_decision'->'predicted'->wan.key->>3)::double precision AS forecast_utilization_30s_pct,
(payload->'last_decision'->'scores'->>wan.key)::double precision AS decision_score
FROM state CROSS JOIN LATERAL jsonb_each(payload->'telemetry') AS wan
WHERE {STREAM} AND state.key='service_status' ORDER BY wan.key"""
    forecast_panel = panel(
        18, "ML 30s forecasts and measured WAN quality", "table", forecasts, 0, 23, 24, 7
    )
    forecast_panel["description"] = (
        "Forecasts combine the experimental XGBoost model with persistence for disabled metrics. "
        "Lower decision score is preferred among paths meeting the current SLA. "
        "Blank forecasts mean down/stale or insufficient continuous history; they are not zero. "
        "RTT/jitter are ICMP round-trip mean/stddev. Sudden-down failover is a policy rule."
    )
    forecast_panel["fieldConfig"]["defaults"].update(decimals=2, noValue="—")
    panels.append(forecast_panel)
    dashboard = {
        "uid": "ai-sdwan",
        "title": "AI SD-WAN",
        "tags": ["SD-WAN", "flexiWAN", "TimescaleDB"],
        "schemaVersion": 41,
        "version": 1,
        "editable": False,
        "timezone": "browser",
        "refresh": "5s",
        "links": [
            {
                "title": "Control and approvals",
                "type": "link",
                "url": "/d/ai-sdwan?var-stream=__latest_live__&viewPanel=15",
                "targetBlank": False,
            }
        ],
        "time": {"from": "now-1h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "stream",
                    "label": "Data stream",
                    "type": "query",
                    "datasource": SOURCE,
                    "query": STREAM_QUERY,
                    "refresh": 1,
                    "multi": False,
                    "includeAll": False,
                    "current": {"text": "Latest live", "value": LATEST_LIVE},
                }
            ]
        },
        "panels": panels,
    }
    target = ROOT / "lab/grafana/dashboards/ai-sdwan.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    print("Built Grafana dashboard: 18 panels, read-only SQL and authenticated API controls.")


if __name__ == "__main__":
    build()

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


def workload_series(identifier, title, sql, x, y, *, unit, width=12):
    item = panel(
        identifier, title, "timeseries", sql, x, y, width, 8, unit=unit, format="time_series"
    )
    item["fieldConfig"]["defaults"].update(
        decimals=2,
        noValue="—",
        custom={
            "drawStyle": "line",
            "lineInterpolation": "linear",
            "lineWidth": 2,
            "fillOpacity": 8,
            "spanNulls": False,
            "showPoints": "auto",
        },
    )
    item["description"] = (
        "Measured at the workload endpoint using real packets in the lab. "
        "Failed or missing measurements are gaps, not zero. "
        "The WAN capacities are emulated; these are not measurements from three real ISPs."
    )
    return item


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
        "WHEN payload->>'kind'='real_traffic' THEN CASE "
        "WHEN payload->>'active'='true' AND updated_at > now()-INTERVAL '25 seconds' "
        "THEN 'Real traffic; capacity-emulated WANs (100 / 80 / 30 Mbps)' "
        "WHEN payload->>'active'='true' THEN 'Real traffic status stale (check runner)' "
        "ELSE 'Real traffic stopped; capacity-emulated WANs' END "
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
    workload_status = f"""SELECT CASE
WHEN payload->>'active'='true' AND updated_at > now()-INTERVAL '25 seconds' THEN 'RUNNING'
WHEN payload->>'active'='true' THEN 'STALE (check runner)' ELSE 'STOPPED' END AS status,
payload->>'run_id' AS run_id, payload->>'phase' AS phase,
COALESCE(payload->>'mode',payload->>'kind') AS traffic_mode,
payload->>'capacity_emulated' AS capacity_emulated,
(payload->>'delay_injected_ms')::double precision AS injected_delay_ms,
(payload->>'loss_injected_pct')::double precision AS injected_loss_pct,
updated_at AS last_heartbeat,
extract(epoch from now()-updated_at)::double precision AS heartbeat_age_seconds,
payload->'application'->'http'->>'success' AS latest_http_success,
payload->'application'->'download'->>'success' AS latest_download_success,
payload->'application'->'udp'->>'success' AS latest_udp_success
FROM state WHERE {STREAM} AND key='application_workload'"""
    status_panel = panel(
        19, "Real traffic workload session", "table", workload_status, 0, 54, 24, 5
    )
    status_panel["description"] = (
        "Workload runner heartbeat and latest endpoint measurement outcomes. "
        "A stale or stopped session is not a current healthy measurement. "
        "Capacity-emulated lab WANs carry real packets; no real ISP behavior is implied."
    )
    status_panel["fieldConfig"]["defaults"]["noValue"] = "—"
    panels.append(status_panel)
    http = f"""SELECT observed_at AS time, 'HTTP response' AS metric,
CASE WHEN payload->>'success'='true' THEN (payload->>'latency_ms')::double precision END AS value
FROM events WHERE {STREAM} AND kind='application_measurement'
AND payload->>'application'='http' AND $__timeFilter(observed_at) ORDER BY observed_at,id"""
    panels.append(workload_series(20, "Endpoint HTTP response time", http, 0, 59, unit="ms"))
    download = f"""SELECT observed_at AS time, 'Received download' AS metric,
CASE WHEN payload->>'success'='true' THEN (payload->>'received_mbps')::double precision END AS value
FROM events WHERE {STREAM} AND kind='application_measurement'
AND payload->>'application'='download' AND $__timeFilter(observed_at) ORDER BY observed_at,id"""
    panels.append(workload_series(21, "Endpoint download goodput", download, 12, 59, unit="Mbits"))
    udp_loss = f"""SELECT observed_at AS time, 'UDP received loss' AS metric,
CASE WHEN payload->>'success'='true' THEN (payload->>'loss_pct')::double precision END AS value
FROM events WHERE {STREAM} AND kind='application_measurement'
AND payload->>'application'='udp' AND $__timeFilter(observed_at) ORDER BY observed_at,id"""
    panels.append(workload_series(22, "Endpoint UDP packet loss", udp_loss, 0, 67, unit="percent"))
    udp_timing = f"""SELECT observed_at AS time, timing.metric,
CASE WHEN payload->>'success'='true' THEN timing.value END AS value
FROM events CROSS JOIN LATERAL (VALUES
('UDP RTT stddev', (payload->>'jitter_ms')::double precision),
('Measured RTT', COALESCE(payload->>'rtt_ms',payload->>'latency_ms')::double precision)
) AS timing(metric,value)
WHERE {STREAM} AND kind='application_measurement'
AND payload->>'application'='udp' AND $__timeFilter(observed_at) ORDER BY observed_at,id,metric"""
    timing_panel = workload_series(
        23, "Endpoint UDP RTT / RTT variation", udp_timing, 12, 67, unit="ms"
    )
    timing_panel["description"] += (
        " UDP echo reports mean round-trip latency and its standard deviation. "
        "RTT is shown only when an echo is received; it is not converted into one-way latency."
    )
    panels.append(timing_panel)
    background = f"""SELECT observed_at AS time,
upper(payload->>'link_id') || ' ' || COALESCE(payload->>'direction',payload->>'side','') || ' ' || traffic.metric AS metric,
CASE WHEN payload->>'success'='true' THEN traffic.value END AS value
FROM events CROSS JOIN LATERAL (VALUES
('offered', (payload->>'offered_mbps')::double precision),
('received', (payload->>'received_mbps')::double precision)
) AS traffic(metric,value)
WHERE {STREAM} AND kind='background_measurement'
AND payload->>'link_id' IN ('wan1','wan2','wan3') AND $__timeFilter(observed_at)
ORDER BY observed_at,id,metric"""
    background_panel = workload_series(
        24,
        "Per-WAN background load: offered and received",
        background,
        0,
        75,
        unit="Mbits",
        width=24,
    )
    background_panel["description"] = (
        "Real background packets compete for each WAN's shared bottleneck separately. "
        "Offered is sender-measured throughput; received is receiver-measured goodput. "
        "These external background packets do not enter the edge VPP utilization counters. "
        "Failed or missing measurements are gaps, not zero."
    )
    panels.append(background_panel)
    endpoint_events = f"""SELECT observed_at AS time,
payload->>'run_id' AS run_id, payload->>'phase' AS phase,
payload->>'application' AS application,
CASE WHEN payload->>'success'='true' THEN 'SUCCESS' ELSE 'FAILED / INCOMPLETE' END AS status,
CASE WHEN payload->>'success'='true' THEN (payload->>'latency_ms')::double precision END AS latency_ms,
CASE WHEN payload->>'success'='true' THEN (payload->>'received_mbps')::double precision END AS received_mbps,
CASE WHEN payload->>'success'='true' THEN (payload->>'loss_pct')::double precision END AS loss_pct,
payload->>'error' AS error
FROM events WHERE {STREAM} AND kind='application_measurement' AND $__timeFilter(observed_at)
ORDER BY id DESC LIMIT 100"""
    endpoint_panel = panel(
        25, "Endpoint measurement outcomes", "table", endpoint_events, 0, 83, 24, 8
    )
    endpoint_panel["fieldConfig"]["defaults"].update(decimals=2, noValue="—")
    endpoint_panel["description"] = (
        "Individual measurement times, phases and outcomes remain visible even when a test fails. "
        "Charts show successful measurements only; failed measurements never become healthy zeros."
    )
    panels.append(endpoint_panel)
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
    target.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"Built Grafana dashboard: {len(panels)} panels, read-only SQL and authenticated API controls."
    )


if __name__ == "__main__":
    build()

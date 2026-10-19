"""Poll the installed probe through authenticated flexiManage -> flexiEdge RPC."""

import json
import time

from .domain import LINKS, Telemetry
from .flexiwan import IntegrationError


class FlexiWANTelemetryCollector:
    def __init__(self, client):
        self.client = client

    def collect(self):
        config = self.client.config
        device = config.telemetry_device_id or config.device_ids[0]
        reply = self.client.request(
            "POST",
            f"devices/{device}/send",
            body={
                "entity": "os",
                "api": "exec_timeout",
                "params": {"cmd": config.probe_command, "timeout": 6},
            },
        )
        message = reply.get("message", {})
        if reply.get("ok") != 1 or not isinstance(message, dict) or message.get("returncode") != 0:
            raise IntegrationError("Edge probe failed or device disconnected")
        try:
            payload = json.loads(message["output"])
            if payload["schema"] != 1 or payload["measurement"] != "icmp_rtt_stddev":
                raise ValueError("Unsupported measurement contract")
            samples = payload["samples"]
            if not isinstance(samples, list) or not 1 <= len(samples) <= 3:
                raise ValueError("Empty or oversized WAN probe")
            names = [row["link_id"] for row in samples]
            if len(set(names)) != len(names) or not set(names) <= set(LINKS):
                raise ValueError("Unknown or duplicated WAN")
            # Quarantine individual rows in Service.ingest; other WANs remain observable.
            parsed = []
            for row in samples:
                try:
                    value = Telemetry.model_validate(row)
                    parsed.append(value)
                except ValueError:
                    continue
            # Independent NTP clients can differ by fractions of a second.
            # Wait for host time to catch up; never rewrite or accept a future timestamp.
            now = time.time()
            ahead = max(
                (value.timestamp - now for value in parsed if 0 < value.timestamp - now <= 1),
                default=0,
            )
            if ahead:
                time.sleep(ahead)
            valid = [value for value in parsed if value.fresh(time.time())]
            if not valid:
                raise ValueError("No valid fresh edge sample")
        except (ValueError, KeyError, TypeError) as error:
            raise IntegrationError(
                "Invalid or stale edge probe; missing data was not replaced with zeros"
            ) from error
        return samples


def live_service(
    config_path,
    database_url=None,
    *,
    experimental_forecast=False,
    observe_native_qbr=False,
    stream="live",
    model_dir="artifacts/model",
):
    from .adapters import FlexiWANAdapter
    from .domain import LINK_CONFIG
    from .flexiwan import FlexiWANConfig
    from .service import Service

    config = FlexiWANConfig.load(config_path)
    if any(
        binding.capacity_mbps != LINK_CONFIG[link].capacity_mbps
        for link, binding in config.links.items()
    ):
        raise IntegrationError(
            "Live capacities must match the evaluated 100/80/30 Mbps lab profile"
        )
    adapter = FlexiWANAdapter(config)
    policy, _ = adapter.client.preflight()
    adapter.client.readback(policy)
    current = adapter.client.current_path(policy)
    if (current is None) != observe_native_qbr:
        adapter.client.close()
        raise IntegrationError(
            "Native QBR requires observation mode; priority mode requires a fixed WAN"
        )
    service = Service(
        database_url,
        adapter=adapter,
        stream=stream,
        forecast_sampling="causal_linear_5s" if experimental_forecast else "strict",
        model_dir=model_dir,
    )
    service.observing = observe_native_qbr
    if observe_native_qbr:
        adapter.backend = "flexiwan_observe"
    service.configure(mode="manual")
    service.controller.current = current
    service.store.put("route", {"current": service.controller.current})
    # Forecast use remains explicit until validated with fresh end-to-end traffic.
    if not experimental_forecast:
        service.model = None
        service.model_error = (
            "Forecast disabled on live probe data; reactive routing until live validation"
        )
    elif service.model is not None:
        service.model_error = (
            "Experimental forecast enabled on real probes; live validation pending"
        )
    service.collector = FlexiWANTelemetryCollector(adapter.client)
    return service

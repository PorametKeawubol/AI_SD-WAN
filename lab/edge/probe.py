#!/usr/bin/env python3
"""Read real VPP counters and probe all three paths. Python 3.8+; no ML packages.

The pinned flexiEdge punts ICMP replies to its Linux TAP. Bind Linux ping to
that verified TAP so replies are measured while packets still traverse VPP.

Targets must be unique per WAN (remote edge underlay IP), so routing cannot send
all probes over the currently selected overlay. ICMP RTT and RTT stddev are
proxies, not one-way delay or RFC 3550 media jitter.
"""

import argparse
import concurrent.futures
import ipaddress
import json
import math
import re
import statistics
import subprocess
import time
from pathlib import Path


def inject_lab_fault(samples, fault, now):
    """Apply only a fresh, explicitly scoped lab telemetry fault."""
    if not 0 <= now - fault.get("created_at", 0) <= 180:
        return samples
    links = fault.get("links", ["wan1"])  # Existing v1 markers target WAN1.
    if not isinstance(links, list) or not links or not set(links) <= {"wan1", "wan2", "wan3"}:
        raise ValueError("Invalid lab telemetry fault targets")
    if fault.get("mode") == "missing":
        return [row for row in samples if row["link_id"] not in links]
    if fault.get("mode") == "corrupt":
        return [{**row, "loss_pct": 101} if row["link_id"] in links else row for row in samples]
    if fault.get("mode") != "none":
        raise ValueError("Unknown lab telemetry fault")
    return samples


def command(arguments, timeout=4):
    result = subprocess.run(
        ["vppctl"] + arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
        restore_signals=False,  # Legacy vppctl writes after CLI close; retain Python's ignored SIGPIPE.
    )
    if result.stderr.strip():
        raise RuntimeError("VPP command reported an error")
    return result.stdout


def counters(text, interface):
    blocks = re.split(r"(?m)^(?=\S+\s+\d+\s+(?:up|down)\b)", text)
    for block in blocks:
        match = re.match(r"(\S+)\s+\d+\s+(up|down)\b", block)
        if match and match.group(1) == interface:
            # VPP omits zero counters, as confirmed in flexiagent fwutils parser.
            rx = re.search(r"\brx bytes\s+(\d+)", block)
            tx = re.search(r"\btx bytes\s+(\d+)", block)
            return (
                match.group(2) == "up",
                int(rx.group(1)) if rx else 0,
                int(tx.group(1)) if tx else 0,
            )
    raise ValueError("Configured VPP interface missing")


def ping_metrics(text):
    summary = re.search(r"(\d+) sent, (\d+) received, ([\d.]+)% packet loss", text)
    if not summary:
        raise ValueError("Missing VPP ping summary")
    return measured_ping(summary, text)


def linux_ping_metrics(text):
    summary = re.search(r"(\d+) packets transmitted, (\d+) received,", text)
    if not summary:
        raise ValueError("Missing Linux ping summary")
    return measured_ping(summary, text)


def measured_ping(summary, text):
    sent, received = int(summary.group(1)), int(summary.group(2))
    values = [float(value) for value in re.findall(r"time=\s*([\d.]+)\s*ms", text)]
    if (
        sent <= 0
        or received > sent
        or len(values) != received
        or not all(math.isfinite(v) for v in values)
    ):
        raise ValueError("Incomplete VPP ping measurements")
    if received == 0:
        # Conservative sentinels accompany up=False; they are never healthy zeros.
        return False, 10000.0, 5000.0, 100.0
    return True, statistics.mean(values), statistics.pstdev(values), 100 * (sent - received) / sent


def linux_tap_ping(binding):
    interface = binding.get("linux_interface", "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ValueError("Invalid Linux TAP interface")
    result = subprocess.run(
        ["ip", "-json", "-details", "link", "show", "dev", interface],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    devices = json.loads(result.stdout)
    if (
        len(devices) != 1
        or devices[0].get("linkinfo", {}).get("info_kind") != "tun"
        or devices[0]["linkinfo"].get("info_data", {}).get("type") != "tap"
        or devices[0].get("address", "").lower() != binding.get("mac", "").lower()
    ):
        raise ValueError("Probe source must be the verified VPP TAP with its assigned WAN MAC")
    result = subprocess.run(
        ["ping", "-n", "-I", interface, "-c", "100", "-i", "0.02", "-W", "1", binding["target_ip"]],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if result.returncode not in (0, 1) or not re.search(
        r"\b100 packets transmitted,", result.stdout
    ):
        raise RuntimeError("Linux TAP probe did not complete its 100-packet window")
    return linux_ping_metrics(result.stdout)


def measure(config):
    bindings = config["links"]
    method = config.get("probe_method", "vpp_cli")
    if method not in {"vpp_cli", "linux_tap"}:
        raise ValueError("Unknown WAN probe method")
    if set(bindings) != {"wan1", "wan2", "wan3"}:
        raise ValueError("Exactly three WAN bindings required")
    targets = []
    for binding in bindings.values():
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", binding["vpp_interface"]):
            raise ValueError("Invalid interface name")
        targets.append(str(ipaddress.IPv4Address(binding["target_ip"])))
        if not 0 < float(binding["capacity_mbps"]) <= 100000:
            raise ValueError("Invalid capacity")
    if len(set(targets)) != 3:
        raise ValueError("WAN probe targets must be distinct")
    first = command(["show", "interface"])
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = (
            {link: pool.submit(linux_tap_ping, binding) for link, binding in bindings.items()}
            if method == "linux_tap"
            else {
                link: pool.submit(
                    command,
                    [
                        "ping",
                        binding["target_ip"],
                        "source",
                        binding["vpp_interface"],
                        "repeat",
                        "100",
                        "interval",
                        "0.02",
                    ],
                    4,
                )
                for link, binding in bindings.items()
            }
        )
        results = {}
        for link, future in futures.items():
            try:
                value = future.result()
                results[link] = value if method == "linux_tap" else ping_metrics(value)
            except (ValueError, RuntimeError, subprocess.SubprocessError):
                # Missing WAN measurement stays missing, and ages out in the controller.
                continue
    second = command(["show", "interface"])
    elapsed = time.monotonic() - started
    if not 0 < elapsed <= 5:
        raise ValueError("Probe exceeded the telemetry cadence")
    now = time.time()
    samples = []
    for link, binding in bindings.items():
        if link not in results:
            continue
        interface_up, rx0, tx0 = counters(first, binding["vpp_interface"])
        up1, rx1, tx1 = counters(second, binding["vpp_interface"])
        if rx1 < rx0 or tx1 < tx0:
            raise ValueError("VPP counter reset during collection")
        # Full duplex utilization = busier direction, not rx+tx.
        utilization = min(
            100, max(rx1 - rx0, tx1 - tx0) * 8 / elapsed / 1e6 / binding["capacity_mbps"] * 100
        )
        reachable, latency, jitter, loss = results[link]
        samples.append(
            {
                "link_id": link,
                "timestamp": now,
                "up": bool(interface_up and up1 and reachable),
                "latency_ms": latency,
                "jitter_ms": jitter,
                "loss_pct": loss,
                "utilization_pct": utilization,
            }
        )
    if config.get("lab_faults_enabled"):
        path = Path("/run/ai-sdwan/lab-fault.json")
        if path.exists():
            fault = json.loads(path.read_text(encoding="utf-8"))
            samples = inject_lab_fault(samples, fault, time.time())
    return {
        "schema": 1,
        "measurement": "icmp_rtt_stddev",
        "probe_origin": "linux_tap_through_vpp" if method == "linux_tap" else "vpp_cli",
        "samples": samples,
        "probe_packets_per_wan": 100,
        "counter_interval_seconds": elapsed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    configuration = json.loads(Path(args.config).read_text(encoding="utf-8"))
    print(json.dumps(measure(configuration), allow_nan=False))

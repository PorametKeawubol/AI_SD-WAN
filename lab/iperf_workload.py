"""Scoped iperf3 workloads and receiver measurements for the isolated WAN lab.

Background clients originate in the exact WAN namespace and bind its bridge
address. They cannot follow the overlay policy onto a different WAN. Servers
run in dedicated receiver namespaces beyond that WAN's bottleneck. Helpers construct
argv or parse results; process ownership and cleanup belong to the runner.
"""

import json
import math

from lab.impairments import CAPACITY


def _endpoint(link, side):
    if link not in CAPACITY or side not in {"a", "b"}:
        raise ValueError("Choose an owned WAN and edge side a or b")
    index = int(link[-1])
    return f"172.16.{index}.{101 if side == 'a' else 102}", 5310 + index


def _bounded_number(value, minimum, maximum, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{label} outside isolated lab limits")
    return value


def background_server_command(link, side):
    """Foreground receiver; runner must own and stop this exact process."""
    address, port = _endpoint(link, side)
    return ["iperf3", "-s", "-B", address, "-p", str(port), "-J"]


def background_client_command(link, side, mbps, duration):
    """Bounded UDP load with receiver JSON and a finite connection timeout."""
    address, port = _endpoint(link, side)
    _bounded_number(mbps, 0.01, CAPACITY[link] * 1.1, "Offered rate")
    _bounded_number(duration, 1, 600, "Duration")
    return [
        "ip", "netns", "exec", "sdw-" + link,
        "iperf3", "-c", address,
        "-B", f"172.16.{int(link[-1])}.254",
        "-p", str(port), "-u", "-b", f"{mbps:g}M",
        "-t", f"{duration:g}", "-l", "1200", "-i", "1",
        "-J", "--get-server-output", "--connect-timeout", "3000",
    ]


def _number(record, key, minimum=0, maximum=math.inf):
    value = record.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        return None
    return value


def _receiver_record(document, *, server=False):
    end = document.get("end", {})
    if not isinstance(end, dict):
        return None, None
    received = end.get("sum_received")
    if isinstance(received, dict) and received.get("sender") is not True:
        return received, "end.sum_received"
    aggregate = end.get("sum")
    if isinstance(aggregate, dict) and (
        aggregate.get("sender") is False or (server and "sender" not in aggregate)
    ):
        return aggregate, "end.sum"
    streams = end.get("streams", [])
    # Commands deliberately use one stream. Never silently report one stream
    # as the total throughput of an unexpected parallel-stream invocation.
    if isinstance(streams, list) and len(streams) == 1 and isinstance(streams[0], dict):
        for key in ("receiver", "udp"):
            record = streams[0].get(key)
            if isinstance(record, dict) and (
                record.get("sender") is False
                or (key == "receiver" and record.get("sender") is not True)
                or (server and "sender" not in record)
            ):
                return record, f"end.streams[0].{key}"
    return None, None


def _sender_mbps(document):
    end = document.get("end", {})
    if not isinstance(end, dict):
        return None
    sent = end.get("sum_sent")
    if not isinstance(sent, dict):
        sent = end.get("sum")
        if not isinstance(sent, dict) or sent.get("sender") is not True:
            return None
    bits = _number(sent, "bits_per_second")
    return bits / 1e6 if bits is not None else None


def parse_iperf_json(document, *, requested_mbps=None):
    """Keep requested, measured sender and measured receiver rates separate.

    Newer clients expose ``sum_received``. Older UDP clients can expose only
    a sender aggregate, so request the server JSON and prefer its receiver
    aggregate. Missing or failed receivers remain unavailable, never zero and
    never replaced with sender throughput. UDP loss/jitter are receiver values.
    """
    result = {
        "ok": False,
        "receiver_available": False,
        "receiver_source": None,
        "requested_mbps": requested_mbps,
        "sender_mbps": None,
        "received_mbps": None,
        "received_bytes": None,
        "seconds": None,
        "loss_pct": None,
        "jitter_ms": None,
        "lost_packets": None,
        "packets": None,
        "error": None,
    }
    if isinstance(document, (str, bytes)):
        try:
            document = json.loads(document)
        except (ValueError, UnicodeError):
            result["error"] = "Invalid iperf JSON"
            return result
    if not isinstance(document, dict):
        result["error"] = "Invalid iperf JSON object"
        return result
    result["sender_mbps"] = _sender_mbps(document)
    if document.get("error"):
        result["error"] = str(document["error"])
        return result

    server = document.get("server_output_json")
    if isinstance(server, str):
        try:
            server = json.loads(server)
        except ValueError:
            server = None
    record, source = None, None
    if isinstance(server, dict):
        if server.get("error"):
            result["error"] = str(server["error"])
            return result
        record, source = _receiver_record(server, server=True)
        if source:
            source = "server_output_json." + source
    if record is None:
        record, source = _receiver_record(document)
    if record is None:
        result["error"] = "Receiver measurement unavailable"
        return result

    rate = _number(record, "bits_per_second")
    seconds = _number(record, "seconds", minimum=1e-9)
    received_bytes = _number(record, "bytes")
    if rate is None or seconds is None or received_bytes is None:
        result["error"] = "Receiver measurement is incomplete or invalid"
        return result
    result.update(
        ok=True,
        receiver_available=True,
        receiver_source=source,
        received_mbps=rate / 1e6,
        received_bytes=received_bytes,
        seconds=seconds,
        loss_pct=_number(record, "lost_percent", maximum=100),
        jitter_ms=_number(record, "jitter_ms"),
        lost_packets=_number(record, "lost_packets"),
        packets=_number(record, "packets"),
    )
    return result

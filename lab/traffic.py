"""UDP echo packet measurements used across the real flexiEdge data plane.

Linux lab runner executes server/client in separate LAN namespaces. A loopback
verification proves only this measurement tool, never flexiWAN forwarding.
"""

import argparse
import json
import math
import select
import socket
import struct
import time
from contextlib import nullcontext
from pathlib import Path

HEADER = struct.Struct("!Qd")
PACKET_BYTES = 1250


def echo(bind, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((bind, port))
        while True:
            packet, address = server.recvfrom(65535)
            if len(packet) == PACKET_BYTES:
                server.sendto(packet, address)


def flood(target, port, duration, mbps, output):
    """Paced underlay load fixed to WAN1, independent of overlay path selection."""
    if not 1 <= mbps <= 100 or not 1 <= duration <= 600:
        raise ValueError("Underlay load outside isolated lab limits")
    rate = mbps * 1e6 / (PACKET_BYTES * 8)
    started = previous = time.monotonic()
    budget = 0
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        payload = bytes(PACKET_BYTES)
        while time.monotonic() - started < duration:
            now = time.monotonic()
            budget = min(50, budget + (now - previous) * rate)
            previous = now
            count = int(budget)
            for _ in range(count):
                sender.sendto(payload, (target, port))
                sent += 1
            budget -= count
            time.sleep(0.0005)
    elapsed = time.monotonic() - started
    Path(output).write_text(
        json.dumps(
            {
                "target": target,
                "sent_packets": sent,
                "actual_offered_mbps": sent * PACKET_BYTES * 8 / elapsed / 1e6,
                "requested_mbps": mbps,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def probe(target, port, duration, mbps, output, *, source_port=0, interval_output=None):
    packets_per_second = mbps * 1e6 / (PACKET_BYTES * 8)
    if not 1 <= packets_per_second <= 10000 or not 1 <= duration <= 7200:
        raise ValueError("Traffic rate or duration outside lab limits")
    if type(source_port) is not int or not 0 <= source_port <= 65535:
        raise ValueError("Invalid UDP source port")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if interval_output is not None:
        interval_output = Path(interval_output)
        if interval_output.resolve() == output.resolve():
            raise ValueError("Interval JSONL and packet JSON paths must differ")
        interval_output.parent.mkdir(parents=True, exist_ok=True)
    interval = 1 / packets_per_second
    started_at = time.time()
    started = time.monotonic()
    bins = [{"sent": 0, "received": 0, "mean_rtt": 0, "m2_rtt": 0} for _ in range(int(duration))]
    next_send, sequence = started, 0
    outstanding = {}
    next_expiry = started + 1
    emitted = 0
    interval_context = (
        interval_output.open("w", encoding="utf-8") if interval_output else nullcontext(None)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, interval_context as stream:

        def emit_completed(now, *, final=False):
            nonlocal emitted
            while emitted < len(bins) and (final or now >= started + emitted + 2):
                bucket = bins[emitted]
                if stream is not None:
                    row = {
                        "schema": 1,
                        "application": "udp",
                        "measurement": "udp_echo_rtt_stddev",
                        "timestamp": started_at + emitted,
                        "second": emitted,
                        "observed_at": time.time(),
                        "target": target,
                        "destination_port": port,
                        "source_port": client.getsockname()[1],
                        "sent_packets": bucket["sent"],
                        "received_packets": bucket["received"],
                        "success": bucket["received"] > 0,
                        "loss_pct": 100 * (bucket["sent"] - bucket["received"]) / bucket["sent"]
                        if bucket["sent"]
                        else None,
                        "latency_ms": bucket["mean_rtt"] if bucket["received"] else None,
                        "jitter_ms": math.sqrt(max(0, bucket["m2_rtt"] / bucket["received"]))
                        if bucket["received"]
                        else None,
                        "received_mbps": bucket["received"] * PACKET_BYTES * 8 / 1e6,
                        "offered_mbps": bucket["sent"] * PACKET_BYTES * 8 / 1e6,
                    }
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                    stream.flush()
                emitted += 1

        client.bind(("0.0.0.0", source_port))
        client.connect((target, port))
        client.setblocking(False)
        while time.monotonic() < started + duration + 1:
            now = time.monotonic()
            # A bin closes only after the final packet's one-second echo grace.
            emit_completed(now)
            if now >= next_expiry:
                outstanding = {
                    identifier: value
                    for identifier, value in outstanding.items()
                    if now - value[0] <= 1
                }
                next_expiry = now + 1
            if now >= next_send and now < started + duration:
                bucket = min(len(bins) - 1, int(now - started))
                packet = HEADER.pack(sequence, now) + bytes(PACKET_BYTES - HEADER.size)
                try:
                    client.send(packet)
                except (BlockingIOError, ConnectionError):
                    pass
                bins[bucket]["sent"] += 1
                outstanding[sequence] = (now, bucket)
                sequence += 1
                # Do not flood a burst of packets to catch up after CPU scheduling stalls.
                next_send = max(next_send + interval, now)
            ready, _, _ = select.select(
                [client],
                [],
                [],
                min(0.005, max(0, next_send - time.monotonic()))
                if now < started + duration
                else 0.005,
            )
            if ready:
                try:
                    packet = client.recv(65535)
                except ConnectionError:
                    continue
                if len(packet) != PACKET_BYTES:
                    continue
                identifier, _ = HEADER.unpack(packet[: HEADER.size])
                if identifier in outstanding:
                    sent_at, bucket = outstanding.pop(identifier)
                    elapsed = time.monotonic() - sent_at
                    if elapsed <= 1:
                        bins[bucket]["received"] += 1
                        delta = elapsed * 1000 - bins[bucket]["mean_rtt"]
                        bins[bucket]["mean_rtt"] += delta / bins[bucket]["received"]
                        bins[bucket]["m2_rtt"] += delta * (
                            elapsed * 1000 - bins[bucket]["mean_rtt"]
                        )
        emit_completed(time.monotonic(), final=True)
        rows = []
        for index, bucket in enumerate(bins):
            rows.append(
                {
                    "second": index,
                    "sent_packets": bucket["sent"],
                    "received_packets": bucket["received"],
                    "loss_pct": 100
                    * (bucket["sent"] - bucket["received"])
                    / max(1, bucket["sent"]),
                    "latency_ms": bucket["mean_rtt"] if bucket["received"] else 10000,
                    "jitter_ms": math.sqrt(max(0, bucket["m2_rtt"] / bucket["received"]))
                    if bucket["received"]
                    else 5000,
                    "received_mbps": bucket["received"] * PACKET_BYTES * 8 / 1e6,
                    "offered_mbps": bucket["sent"] * PACKET_BYTES * 8 / 1e6,
                    "measurement": "udp_echo_rtt_stddev",
                }
            )
        output.write_text(
            json.dumps(
                {
                    "target": target,
                    "destination_port": port,
                    "source_ip": client.getsockname()[0],
                    "source_port": client.getsockname()[1],
                    "started_at": started_at,
                    "requested_mbps": mbps,
                    "packet_bytes": PACKET_BYTES,
                    "intervals": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["server", "client", "flood"])
    parser.add_argument("--host", default="10.30.0.2")
    parser.add_argument("--port", type=int, default=5209)
    parser.add_argument("--source-port", type=int, default=0)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--mbps", type=float, default=2)
    parser.add_argument("--output", default="reports/live/packets.json")
    parser.add_argument("--interval-output", help="Flush completed UDP intervals as JSONL")
    args = parser.parse_args()
    if args.action == "server":
        echo(args.host, args.port)
    elif args.action == "flood":
        flood(args.host, args.port, args.duration, args.mbps, args.output)
    else:
        probe(
            args.host,
            args.port,
            args.duration,
            args.mbps,
            args.output,
            source_port=args.source_port,
            interval_output=args.interval_output,
        )

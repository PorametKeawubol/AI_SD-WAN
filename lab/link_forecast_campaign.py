"""Measured link-only forecast campaign, independent of flexiEdge and its runtime.

Creates six private network namespaces, sends real UDP load and ICMP probes,
and varies NetEm conditions. No controller, existing namespace or VM is changed.
This is a link experiment, never a flexiWAN routing or ISP benchmark.
"""

import argparse
import concurrent.futures
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from lab.edge.probe import linux_ping_metrics
from sdwan.domain import LINKS, Telemetry

CAPACITIES = (100, 80, 30)
SPLIT = {"train": 4, "tune": 2, "accept": 3, "holdout": 3}


def conditions(seed, link_index, elapsed):
    """A fixed mixed suite: stable, ramp, step and recovery; no model input."""
    rng = random.Random(seed + 1009 * link_index)
    base = (12, 20, 32.5)[link_index]
    amplitude = rng.uniform(20, 65)
    offset = rng.uniform(-3, 3)
    # Random phase/duration prevents identical trajectories across episodes.
    phase = (elapsed + rng.uniform(0, 35)) % rng.uniform(95, 125)
    if phase < 20:
        factor, regime = 0.0, "stable"
    elif phase < 55:
        factor, regime = (phase - 20) / 35, "ramp"
    elif phase < 75:
        factor, regime = 1.0, "step_busy"
    else:
        factor, regime = max(0, 1 - (phase - 75) / 25), "recovery"
    # Deterministic disturbance is confined to the network configuration.
    noise = random.Random(seed + link_index * 1009 + int(elapsed // 5)).uniform(-1, 1)
    return {
        "delay_ms": max(1, base + offset + amplitude * factor + noise),
        "jitter_ms": 1.0 + 4.0 * factor,
        "loss_pct": (rng.choice((0.0, 0.3, 1.0)) if regime == "step_busy" else 0.0),
        "load_fraction": 0.10 + rng.uniform(0.25, 0.65) * factor,
        "regime": regime,
    }


def plan(seed_base=20261700, counts=None):
    partitions = dict(SPLIT) if counts is None else dict(zip(SPLIT, counts))
    if (
        len(partitions) != 4
        or (counts is not None and len(counts) != 4)
        or any(not isinstance(value, int) or value < 0 for value in partitions.values())
        or sum(partitions.values()) == 0
        or not isinstance(seed_base, int)
        or seed_base < 0
    ):
        raise ValueError('Require four nonnegative partition counts and a nonnegative seed')
    return {
        "schema": 1,
        "testbed": "isolated_linux_veth_netem",
        "real_packets": True,
        "flexiwan_in_path": False,
        "real_isp": False,
        "duration_seconds_per_episode": 125,
        "interval_seconds": 5,
        "partitions": partitions,
        "seeds": [seed_base + 7919 * i for i in range(sum(partitions.values()))],
        "capacity_mbps": dict(zip(LINKS, CAPACITIES)),
        "scenarios": ["stable", "gradual_degradation", "step_busy", "recovery"],
        "measurements": {
            "rtt_jitter_loss": "100 ICMP probes at 20 ms spacing; RTT mean and population stddev",
            "utilization": "measured source veth TX bytes / actual elapsed interval / capacity; not VPP",
            "raw_counter_audit": "per-frame TX counter before/after, elapsed seconds and configured Mbps",
        },
        "split_rule": "whole chronological episodes; all WANs together; no cross-episode windows",
        "acceptance": "at least 2% MAE gain, >=3 episode groups, >=30 windows, paired episode-block 95% CI upper<0",
        "runtime_promotion": False,
    }


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=10).stdout


def load_worker(target, rate_file, parent):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b"L" * 1200
    while os.getppid() == parent:
        started = time.monotonic()
        rate = float(Path(rate_file).read_text())
        for _ in range(max(1, int(rate * 1e6 * 0.01 / (len(payload) * 8)))):
            sock.sendto(payload, (target, 9999))
        time.sleep(max(0, 0.01 - (time.monotonic() - started)))


def sink_worker(address):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((address, 9999))
    while True:
        sock.recvfrom(2048)


def probe(namespace, target, raw_path):
    result = subprocess.run(
        [
            "ip",
            "netns",
            "exec",
            namespace,
            "ping",
            "-n",
            "-c",
            "100",
            "-i",
            "0.02",
            "-W",
            "1",
            target,
        ],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    with raw_path.open("a") as stream:
        stream.write(
            json.dumps(
                {"observed_at": time.time(), "stdout": result.stdout, "code": result.returncode}
            )
            + "\n"
        )
    if result.returncode not in (0, 1) or "100 packets transmitted" not in result.stdout:
        raise RuntimeError("Incomplete ICMP probe batch")
    return linux_ping_metrics(result.stdout)


def tx_bytes(namespace):
    return int(
        command("ip", "netns", "exec", namespace, "cat", "/sys/class/net/wan/statistics/tx_bytes")
    )


def run(destination, *, seed_base=20261700, counts=None):
    if os.geteuid() != 0 or not Path("/opt/ai-sdwan-lab/.ai-sdwan-owned").exists():
        raise RuntimeError("Run only as root in the owned Linux lab")
    destination.mkdir(parents=True, exist_ok=False)
    protocol = plan(seed_base, counts)
    (destination / "protocol.json").write_text(json.dumps(protocol, indent=2))
    namespaces, children, bindings = [], [], []
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    document = {
        "schema": 1,
        "data_kind": "real_recorded_probe",
        "sampling": "causal_linear_5s",
        "testbed": protocol["testbed"],
        "episodes": [],
    }
    status = {
        "started_at": time.time(),
        "completed": False,
        "cleanup_complete": False,
        "testbed": protocol["testbed"],
        "errors": [],
    }
    prefix = "ml" + str(os.getpid())
    try:
        for i, capacity in enumerate(CAPACITIES):
            source, sink = f"{prefix}-{i}a", f"{prefix}-{i}b"
            # ip netns add refuses an existing namespace; only successful creates are owned.
            for namespace in (source, sink):
                command("ip", "netns", "add", namespace)
                namespaces.append(namespace)
                command("ip", "-n", namespace, "link", "set", "lo", "up")
            left, right = f"{prefix}{i}a", f"{prefix}{i}b"
            command(
                "ip",
                "link",
                "add",
                left,
                "netns",
                source,
                "type",
                "veth",
                "peer",
                "name",
                right,
                "netns",
                sink,
            )
            for namespace, interface, host in ((source, left, 1), (sink, right, 2)):
                command("ip", "-n", namespace, "link", "set", interface, "name", "wan")
                command("ip", "-n", namespace, "addr", "add", f"10.231.{i}.{host}/30", "dev", "wan")
                command("ip", "-n", namespace, "link", "set", "wan", "up")
                command(
                    "ip",
                    "netns",
                    "exec",
                    namespace,
                    "tc",
                    "qdisc",
                    "add",
                    "dev",
                    "wan",
                    "root",
                    "netem",
                    "delay",
                    "1ms",
                    "rate",
                    f"{capacity}mbit",
                )
            target = f"10.231.{i}.2"
            rate_file = destination / f"rate-{i}.txt"
            rate_file.write_text("1")
            for namespace, worker_args in (
                (sink, ["--sink", target]),
                (source, ["--load", target, str(rate_file), str(os.getpid())]),
            ):
                children.append(
                    subprocess.Popen(
                        [
                            "ip",
                            "netns",
                            "exec",
                            namespace,
                            sys.executable,
                            "-m",
                            "lab.link_forecast_campaign",
                            *worker_args,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
            capture = (destination / f"packets-{i}.log").open("w")
            children.append(
                subprocess.Popen(
                    [
                        "ip",
                        "netns",
                        "exec",
                        source,
                        "tcpdump",
                        "-n",
                        "-U",
                        "-i",
                        "wan",
                        "-s",
                        "128",
                        "-c",
                        "1000",
                        "-w",
                        str(destination / f"packets-{i}.pcap"),
                    ],
                    stdout=capture,
                    stderr=capture,
                )
            )
            capture.close()
            bindings.append((source, sink, target, rate_file))
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for episode_index, seed in enumerate(protocol["seeds"]):
                if stopped:
                    raise RuntimeError("Campaign stopped before completion")
                episode = {"id": f"link-{seed}", "series": {link: [] for link in LINKS}}
                events = (destination / f"conditions-{episode_index:02}.jsonl").open("w")
                started, wall = time.monotonic(), time.time()
                previous_bytes = [tx_bytes(binding[0]) for binding in bindings]
                previous_time = started
                for tick in range(25):
                    if stopped:
                        raise RuntimeError("Campaign stopped during episode")
                    time.sleep(max(0, started + tick * 5 - time.monotonic()))
                    now = time.monotonic()
                    if (
                        now - (started + tick * 5) > 0.5
                        or abs((time.time() - wall) - (now - started)) > 1
                    ):
                        raise RuntimeError("Clock or cadence interruption; campaign invalid")
                    frame = []
                    for i, (source, sink, target, rate_file) in enumerate(bindings):
                        config = conditions(seed, i, tick * 5)
                        frame.append(config)
                        for namespace in (source, sink):
                            command(
                                "ip",
                                "netns",
                                "exec",
                                namespace,
                                "tc",
                                "qdisc",
                                "change",
                                "dev",
                                "wan",
                                "root",
                                "netem",
                                "delay",
                                f"{config['delay_ms']:.3f}ms",
                                f"{config['jitter_ms']:.3f}ms",
                                "distribution",
                                "normal",
                                "loss",
                                f"{config['loss_pct']:.3f}%",
                                "rate",
                                f"{CAPACITIES[i]}mbit",
                            )
                        temporary = rate_file.with_suffix(".tmp")
                        temporary.write_text(str(CAPACITIES[i] * config["load_fraction"]))
                        temporary.replace(rate_file)
                    events.write(json.dumps({"tick": tick, "config": frame}) + "\n")
                    events.flush()
                    futures = [
                        pool.submit(
                            probe,
                            binding[0],
                            binding[2],
                            destination / f"probes-{episode_index:02}-{i}.jsonl",
                        )
                        for i, binding in enumerate(bindings)
                    ]
                    results = [future.result() for future in futures]
                    observed = time.time()
                    current_time = time.monotonic()
                    for i, (link, result) in enumerate(zip(LINKS, results)):
                        count = tx_bytes(bindings[i][0])
                        if count < previous_bytes[i] or current_time <= previous_time:
                            raise RuntimeError('Counter reset or nonpositive elapsed interval')
                        utilization = (
                            100
                            * (count - previous_bytes[i])
                            * 8
                            / ((current_time - previous_time) * CAPACITIES[i] * 1e6)
                        )
                        audit = {
                            "tick": tick,
                            "link_id": link,
                            "observed_at": observed,
                            "tx_bytes_before": previous_bytes[i],
                            "tx_bytes_after": count,
                            "elapsed_seconds": current_time - previous_time,
                            "configured_mbps": CAPACITIES[i],
                            "raw_utilization_pct": utilization,
                            "utilization_pct": min(100, max(0, utilization)),
                        }
                        with (destination / f"counters-{episode_index:02}.jsonl").open('a') as log:
                            log.write(json.dumps(audit, allow_nan=False) + '\n')
                        previous_bytes[i] = count
                        up, rtt, jitter, loss = result
                        sample = Telemetry(
                            link_id=link,
                            timestamp=observed,
                            up=up,
                            latency_ms=rtt,
                            jitter_ms=jitter,
                            loss_pct=loss,
                            utilization_pct=min(100, max(0, utilization)),
                        )
                        episode["series"][link].append(sample.model_dump())
                    previous_time = current_time
                events.close()
                document["episodes"].append(episode)
                (destination / "dataset.json").write_text(json.dumps(document, allow_nan=False))
                status.update(episodes_completed=len(document["episodes"]), updated_at=time.time())
                (destination / "status.json").write_text(json.dumps(status, indent=2))
                print(
                    f"episode {episode_index + 1}/{len(protocol['seeds'])} complete; "
                    "75 measured WAN frames", flush=True
                )
        status["completed"] = True
    except BaseException as error:
        status["errors"].append(f"{type(error).__name__}: {error}")
        raise
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for namespace in reversed(namespaces):
            command("ip", "netns", "delete", namespace)
        status.update(cleanup_complete=True, ended_at=time.time())
        (destination / "status.json").write_text(json.dumps(status, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--seed-base", type=int, default=20261700)
    parser.add_argument("--counts", nargs=4, type=int)
    parser.add_argument("--sink")
    parser.add_argument("--load", nargs=3)
    args = parser.parse_args()
    if args.sink:
        sink_worker(args.sink)
    elif args.load:
        load_worker(args.load[0], args.load[1], int(args.load[2]))
    elif args.plan:
        print(json.dumps(plan(args.seed_base, args.counts), indent=2))
    elif args.output:
        run(args.output.resolve(), seed_base=args.seed_base, counts=args.counts)
    else:
        parser.error("--output or --plan required")


if __name__ == "__main__":
    main()

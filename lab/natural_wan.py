"""Time-varying packet emulation in the owned lab; never fabricate telemetry.

Fiber/broadband/mobile are illustrative profiles, not calibrated ISP traces.
Nominal capacities stay 100/80/30 Mbps, matching the collector's denominators.
"""

import argparse
import hashlib
import json
import math
import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from lab.impairments import CAPACITY, plan, require_linux, shaping_session

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "lab/runtime/natural-wan.json"
PROFILES = {
    "wan1": {
        "name": "Fiber",
        "delay_ms": 12,
        "jitter_ms": 1.5,
        "correlation": 20,
        "states": {"calm": (0.9, 100, 220, 0, 0, 6, 12), "busy": (0.1, 30, 60, 7, 1.5, 12, 18)},
        "loss": {"calm": (0.002, 50, 5, 0.002), "busy": (0.01, 40, 10, 0.005)},
    },
    "wan2": {
        "name": "Broadband",
        "delay_ms": 20,
        "jitter_ms": 4,
        "correlation": 35,
        "states": {"calm": (0.6, 35, 80, 0, 0, 10, 18), "busy": (0.4, 30, 75, 20, 6, 22, 36)},
        "loss": {"calm": (0.02, 30, 20, 0.01), "busy": (0.08, 25, 30, 0.04)},
    },
    "wan3": {
        "name": "Mobile",
        "delay_ms": 32.5,
        "jitter_ms": 6,
        "correlation": 50,
        "states": {
            "calm": (0.5, 20, 45, 0, 0, 8, 18),
            "busy": (0.35, 20, 45, 17, 5, 18, 32),
            "fade": (0.15, 8, 18, 42, 12, 24, 40),
        },
        "loss": {
            "calm": (0.08, 25, 40, 0.03),
            "busy": (0.2, 15, 60, 0.08),
            "fade": (0.6, 15, 70, 0.1),
        },
    },
}


class NaturalModel:
    """Independent seeded regimes plus correlated, bounded fluctuations."""

    def __init__(self, seed=20261009):
        self.seed = seed
        self.previous = -1.0
        self.links = {}
        for index, (link, profile) in enumerate(PROFILES.items()):
            rng = random.Random(seed + 1009 * index)
            initial = profile["states"]["calm"]
            self.links[link] = {
                "rng": rng,
                "state": "calm",
                "until": rng.uniform(initial[1], initial[2]),
                "noise": 0.0,
                "delay": profile["delay_ms"],
                "jitter": profile["jitter_ms"],
                "load": rng.uniform(initial[5], initial[6]),
            }

    def step(self, elapsed):
        if not math.isfinite(elapsed) or elapsed < 0 or elapsed <= self.previous:
            raise ValueError("Profile time must strictly advance")
        self.previous = elapsed
        settings, loads, states = {}, {}, {}
        for link, profile in PROFILES.items():
            current, rng = self.links[link], self.links[link]["rng"]
            if elapsed >= current["until"]:
                names = list(profile["states"])
                current["state"] = rng.choices(
                    names, weights=[profile["states"][s][0] for s in names]
                )[0]
                limits = profile["states"][current["state"]]
                current["until"] = elapsed + rng.uniform(limits[1], limits[2])
            limits = profile["states"][current["state"]]
            current["noise"] = max(-3, min(3, 0.75 * current["noise"] + rng.gauss(0, 0.5)))
            demand = rng.uniform(limits[5], limits[6])
            current["load"] = 0.65 * current["load"] + 0.35 * demand
            target_delay = (
                profile["delay_ms"] + limits[3] + current["noise"] * (1, 2.5, 4)[int(link[-1]) - 1]
            )
            current["delay"] = max(5, 0.65 * current["delay"] + 0.35 * target_delay)
            target_jitter = profile["jitter_ms"] + limits[4] + abs(current["noise"])
            current["jitter"] = min(
                current["delay"] * 0.4, 0.65 * current["jitter"] + 0.35 * target_jitter
            )
            loss = profile["loss"][current["state"]]
            settings[link] = {
                "delay_ms": round(current["delay"], 3),
                "jitter_ms": round(current["jitter"], 3),
                "delay_correlation_pct": profile["correlation"],
                "rate_mbps": CAPACITY[link],
                "burst_loss": dict(
                    zip(("enter_bad_pct", "exit_bad_pct", "bad_loss_pct", "good_loss_pct"), loss)
                ),
            }
            loads[link] = round(CAPACITY[link] * current["load"] / 100, 3)
            states[link] = current["state"]
        return {"wans": settings, "load_mbps": loads, "regimes": states}


def atomic_state(document):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(STATE)


def require_owned():
    require_linux()
    if not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use the prepared isolated Linux lab")


def inspect_baseline():
    from lab.resume import verified_namespaces, vm_matches

    if not verified_namespaces():
        raise RuntimeError("The complete owned WAN topology must already be running")
    for side in ("a", "b"):
        xml = subprocess.check_output(["virsh", "dumpxml", "sdw-edge-" + side], text=True)
        if not vm_matches(xml, side):
            raise RuntimeError("VM identity differs; existing resources were preserved")
    snapshot = {}
    baseline = plan("CONTROL")["wans"]
    for link in CAPACITY:
        snapshot[link] = {}
        for side in ("a", "b"):
            output = subprocess.check_output(
                ["ip", "netns", "exec", "sdw-" + link, "tc", "-j", "qdisc", "show", "dev", side],
                text=True,
            )
            qdiscs = json.loads(output)
            if len(qdiscs) != 1 or qdiscs[0]["kind"] != "netem":
                raise RuntimeError("Unknown qdisc preserved; restore the lab baseline first")
            options = qdiscs[0]["options"]
            expected = baseline[link]
            if (
                not math.isclose(options["delay"]["delay"] * 1000, expected["delay_ms"])
                or not math.isclose(options["delay"]["jitter"] * 1000, expected["jitter_ms"])
                or options["rate"]["rate"] * 8 / 1e6 != expected["rate_mbps"]
                or options["delay"].get("correlation", 0) != 0
                or any(key.startswith("loss") for key in options)
            ):
                raise RuntimeError("A non-baseline impairment is active; it was preserved")
            snapshot[link][side] = qdiscs
    return snapshot


def live_store(stream):
    import ssl

    import httpx

    from sdwan.storage import Store

    private = json.loads((ROOT / "lab/runtime/observability/private.json").read_text())
    store = Store(private["database_url"], stream=stream or "live")
    with store.connection() as connection:
        row = connection.execute(
            "SELECT stream FROM state WHERE key='service_status' AND payload->>'backend'='flexiwan' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if not row or (stream is not None and row["stream"] != stream):
        raise RuntimeError("Choose the currently running decision service's stream")
    store.stream = row["stream"]
    context = ssl.create_default_context(cafile=str(ROOT / "lab/runtime/certs/certificate.pem"))
    with httpx.Client(verify=context, trust_env=False, timeout=10) as client:
        state = (
            client.get(
                "https://localhost:8000/api/state",
                headers={"Authorization": "Bearer " + private["api_token"]},
            )
            .raise_for_status()
            .json()
        )
    if (
        state["backend"] != "flexiwan"
        or set(state["telemetry"]) != set(CAPACITY)
        or not all(row["fresh"] and row["up"] for row in state["telemetry"].values())
    ):
        raise RuntimeError("Three fresh measured WANs are required before emulation")
    return store


def run_environment(args):
    require_owned()
    model = NaturalModel(args.seed)
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with shaping_session() as update:
        snapshot = inspect_baseline()
        store = live_store(args.stream)
        children = []
        document = {
            "active": False,
            "pid": os.getpid(),
            "seed": args.seed,
            "stream": store.stream,
            "started_at": time.time(),
            "profiles": {k: v["name"] for k, v in PROFILES.items()},
            "calibrated_on_real_isp": False,
            "baseline_qdiscs": snapshot,
        }
        start = time.monotonic()
        try:
            frame = model.step(0)
            update(frame, change=True)
            document.update(active=True, updated_at=time.time(), elapsed_seconds=0, **frame)
            atomic_state(document)
            load_log = ROOT / "lab/runtime/logs/natural-wan-load.log"
            load_log.parent.mkdir(parents=True, exist_ok=True)
            with load_log.open("ab") as log:
                for link in CAPACITY:
                    children.append(
                        subprocess.Popen(
                            [
                                "ip",
                                "netns",
                                "exec",
                                "sdw-" + link,
                                sys.executable,
                                "-m",
                                "lab.natural_wan",
                                "load",
                                "--link",
                                link,
                                "--parent",
                                str(os.getpid()),
                            ],
                            cwd=ROOT,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                        )
                    )
            store.log(
                time.time(),
                "natural_wan_started",
                {
                    **{k: v for k, v in document.items() if k != "baseline_qdiscs"},
                    "profile_definitions": PROFILES,
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                },
            )
            while not stop:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("A WAN load worker stopped; restoring the baseline")
                store.put(
                    "wan_environment", {k: v for k, v in document.items() if k != "baseline_qdiscs"}
                )
                store.log(
                    time.time(),
                    "natural_wan_profile",
                    {
                        key: document[key]
                        for key in ("seed", "elapsed_seconds", "wans", "load_mbps", "regimes")
                    },
                )
                deadline = time.monotonic() + args.interval
                while not stop and time.monotonic() < deadline:
                    if args.duration and time.monotonic() - start >= args.duration:
                        stop = True
                        break
                    time.sleep(0.2)
                if stop:
                    break
                frame = model.step(time.monotonic() - start)
                update(frame, change=True)
                document.update(
                    updated_at=time.time(),
                    elapsed_seconds=round(time.monotonic() - start, 2),
                    **frame,
                )
                atomic_state(document)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            update(plan("CONTROL"), change=True)
            document.update(active=False, updated_at=time.time(), restored_baseline=True)
            atomic_state(document)
            store.put(
                "wan_environment", {k: v for k, v in document.items() if k != "baseline_qdiscs"}
            )
            store.log(time.time(), "natural_wan_stopped", {"restored_baseline": True})


def load_worker(link, parent):
    require_owned()
    if Path("/proc/self/ns/net").stat().st_ino != Path("/run/netns/sdw-" + link).stat().st_ino:
        raise RuntimeError("Load must run inside its exact owned WAN namespace")
    from lab.traffic import PACKET_BYTES

    payload = bytes(PACKET_BYTES)
    target = (f"172.16.{int(link[-1])}.1", 9999)
    budget, previous, check_at, mbps = 0.0, time.monotonic(), 0.0, 0.0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        while True:
            now = time.monotonic()
            if now >= check_at:
                document = json.loads(STATE.read_text())
                if (
                    not document["active"]
                    or document["pid"] != parent
                    or not 0 <= time.time() - document["updated_at"] <= 25
                    or not Path(f"/proc/{parent}").exists()
                ):
                    return
                mbps = document["load_mbps"][link]
                if not 0 <= mbps <= CAPACITY[link] * 0.5:
                    raise ValueError("Background load exceeds the bounded profile")
                check_at = now + 1
            budget = min(50, budget + (now - previous) * mbps * 1e6 / (PACKET_BYTES * 8))
            previous = now
            count = int(budget)
            for _ in range(count):
                sender.sendto(payload, target)
            budget -= count
            time.sleep(0.001)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "start", "stop", "status", "load"))
    parser.add_argument("--stream", help="Default: discover the active live API stream")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--duration", type=float, default=0, help="Seconds; 0 runs until stopped")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--link", choices=tuple(CAPACITY))
    parser.add_argument("--parent", type=int)
    args = parser.parse_args()
    if not 5 <= args.interval <= 15 or not math.isfinite(args.duration) or args.duration < 0:
        parser.error("Use interval 5–15 seconds and a nonnegative duration")
    if args.action == "plan":
        model = NaturalModel(args.seed)
        print(
            json.dumps(
                {
                    "illustrative_profiles": True,
                    "nominal_capacity_mbps": CAPACITY,
                    "frames": [model.step(i * 10) for i in range(19)],
                },
                indent=2,
            )
        )
        return
    require_owned()
    if args.action == "load":
        if args.link is None or args.parent is None:
            parser.error("Load worker requires a link and parent")
        load_worker(args.link, args.parent)
    elif args.action == "status":
        print(STATE.read_text() if STATE.exists() else '{"active": false}')
    elif args.action == "stop":
        if not STATE.exists():
            print("Natural WAN mode has not been started.")
            return
        document = json.loads(STATE.read_text())
        if not document.get("active") and document.get("restored_baseline"):
            print("Natural WAN mode is already stopped.")
            return
        process = Path(f"/proc/{document['pid']}/cmdline")
        if process.exists():
            command = process.read_bytes().split(b"\0")
            if b"lab.natural_wan" not in command or b"start" not in command:
                raise RuntimeError("Saved PID is another process; it was preserved")
            os.kill(document["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                current = json.loads(STATE.read_text())
                if not current["active"] and current.get("restored_baseline"):
                    print("Natural WAN mode stopped; baseline restored.")
                    return
                time.sleep(0.2)
            raise RuntimeError("Restoration did not complete; inspect the natural WAN log")
        if document.get("active"):
            with shaping_session() as update:
                update(plan("CONTROL"), change=True)
            document.update(active=False, restored_baseline=True, updated_at=time.time())
            atomic_state(document)
            print("Exited runner's baseline restored.")
        else:
            print("Natural WAN mode is already stopped.")
    elif args.background:
        with shaping_session():
            inspect_baseline()
            store = live_store(args.stream)
        log_path = ROOT / "lab/runtime/logs/natural-wan.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "lab.natural_wan",
                    "start",
                    "--stream",
                    store.stream,
                    "--seed",
                    str(args.seed),
                    "--interval",
                    str(args.interval),
                    "--duration",
                    str(args.duration),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        print(f"Natural WAN starting: PID {child.pid}; stream {store.stream}")
    else:
        run_environment(args)


if __name__ == "__main__":
    main()

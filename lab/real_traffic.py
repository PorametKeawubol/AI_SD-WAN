"""Real application packets and receiver-measured pinned load in the owned lab.

Only WAN capacity is emulated. Never inject latency/loss or write telemetry.
The live collector, policy, journal and both real flexiEdges keep their roles.
"""

import argparse
import json
import math
import os
import shutil
import signal
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from lab.impairments import CAPACITY, shaping_session
from lab.impairments import plan as impairment_plan
from lab.iperf_workload import (
    background_client_command,
    parse_iperf_json,
)
from lab.load_endpoints import create_endpoints, remove_endpoints
from lab.natural_wan import inspect_baseline, live_store, require_owned
from sdwan.adapters import FlexiWANAdapter
from sdwan.flexiwan import FlexiWANConfig, policy_body

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "lab/runtime/real-traffic.json"
CYCLE_SECONDS = 210


def workload_plan(elapsed):
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Elapsed time must be finite and nonnegative")
    tick = elapsed % CYCLE_SECONDS
    phase = "baseline" if tick < 60 else "wan1_busy" if tick < 150 else "recovery"
    rates = {
        link: capacity * fraction
        for (link, capacity), fraction in zip(CAPACITY.items(), (0.15, 0.20, 0.10))
    }
    if phase == "wan1_busy":
        rates["wan1"] = CAPACITY["wan1"] * 1.1
    return {
        "phase": phase,
        "cycle": int(elapsed // CYCLE_SECONDS),
        "per_direction_requested_mbps": rates,
    }


def capacity_only():
    return {
        "wans": {
            link: {"delay_ms": 0, "jitter_ms": 0, "loss_pct": 0, "rate_mbps": capacity}
            for link, capacity in CAPACITY.items()
        }
    }


def atomic_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def connect_api():
    private = json.loads((ROOT / "lab/runtime/observability/private.json").read_text())
    os.environ["FLEXIWAN_ACCESS_KEY"] = json.loads(
        (ROOT / "lab/runtime/credentials.json").read_text()
    )["access_key"]
    return httpx.Client(
        base_url="https://localhost:8000",
        trust_env=False,
        timeout=10,
        verify=ssl.create_default_context(cafile=str(ROOT / "lab/runtime/certs/certificate.pem")),
        headers={"Authorization": "Bearer " + private["api_token"]},
    )


def ensure_classifier(adapter):
    expected = {
        "name": "custom:ai-real-traffic",
        "description": "Owned LAN HTTP/download and UDP real traffic workload",
        "category": "ai-sdwan-lab",
        "serviceClass": "real-time",
        "importance": "high",
        "rules": [
            {"ip": "", "ports": "5209", "protocol": "udp"},
            {"ip": "", "ports": "8088", "protocol": "tcp"},
        ],
    }
    rows = adapter.client.request("GET", "appidentifications")["appIdentifications"]
    matches = [row for row in rows if row["name"] == expected["name"]]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous owned workload classifier")
    actual = (
        adapter.client.request("GET", "appidentifications/custom/" + matches[0]["id"])
        if matches
        else adapter.client.request("POST", "appidentifications", body=expected)
    )
    observed = {key: actual.get(key) for key in expected}
    observed["rules"] = [
        {key: rule.get(key, "") for key in ("ip", "ports", "protocol")}
        for rule in actual.get("rules", [])
    ]
    if observed != expected:
        raise RuntimeError("Existing workload classifier differs; it was preserved")
    return actual["id"]


def wait_idle(http, stopped=None):
    deadline = time.monotonic() + 190
    while time.monotonic() < deadline:
        state = http.get("/api/state").raise_for_status().json()
        if not state.get("apply_inflight"):
            return state
        if stopped and stopped():
            raise RuntimeError("Stopped while waiting for the existing apply")
        time.sleep(1)
    raise RuntimeError("Existing apply did not finish; no policy update sent")


def drain_jsonl(path, offset):
    if not path.exists():
        return [], offset
    rows = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line or not line.endswith(b"\n"):
                return rows, start
            rows.append(json.loads(line))


def wait_receivers(endpoints, receivers):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(receiver.poll() is not None for receiver in receivers):
            raise RuntimeError("A pinned receiver failed during startup")
        ready = True
        for binding in endpoints:
            output = subprocess.check_output(
                [
                    "ip",
                    "netns",
                    "exec",
                    binding["namespace"],
                    "ss",
                    "-H",
                    "-ltn",
                    "sport = :" + str(binding["port"]),
                ],
                text=True,
                timeout=3,
            )
            ready &= f"{binding['address']}:{binding['port']}" in output
        if ready:
            return
        time.sleep(0.2)
    raise RuntimeError("Pinned receivers did not become ready")


def stop_processes(children, captures, guards):
    """Reap owned children while allowing server guards to finish their cleanup."""
    errors = []
    for process in children:
        try:
            if process.poll() is None:
                process.send_signal(signal.SIGINT if process in captures else signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"Process signal {process.pid}: {type(error).__name__}")
    for process in children:
        try:
            # A guard can spend 5 s terminating and another 5 s reaping its child.
            process.wait(timeout=12 if process in guards else 5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"Process cleanup {process.pid}: {type(error).__name__}")
        except OSError as error:
            errors.append(f"Process cleanup {process.pid}: {type(error).__name__}")
    return errors


def run(args):
    require_owned()
    for executable in ("iperf3", "tcpdump", "ss"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"Install {executable} on the owned Linux lab before starting")
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_id = (
        "real-traffic-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    )
    destination = ROOT / "artifacts/real-traffic" / run_id
    children, receivers, guards, loads, captures = [], [], [], [], []
    endpoints = []
    previous = original_classification = None
    changed_classifier = False
    document = {
        "active": False,
        "kind": "real_traffic",
        "pid": os.getpid(),
        "run_id": run_id,
        "started_at": time.time(),
        "capacity_emulated": True,
        "real_isp": False,
        "capacity_mbps": CAPACITY,
        "delay_injected_ms": 0,
        "loss_injected_pct": 0,
        "profiles": {
            link: "Capacity " + str(capacity) + " Mbps" for link, capacity in CAPACITY.items()
        },
        "application": {},
        "background": {},
        "errors": [],
    }
    with shaping_session() as update, connect_api() as http:
        inspect_baseline()
        store = live_store(args.stream)
        adapter = FlexiWANAdapter(FlexiWANConfig.load(ROOT / "config/flexiwan.local.json"))
        document["stream"] = store.stream
        destination.mkdir(parents=True, exist_ok=False)
        offsets = {"http": 0, "udp": 0}
        app_files = {}
        phase, background_at, measurement_count = None, -100.0, 0
        start = time.monotonic()
        with (destination / "processes.log").open("ab") as log:

            def child(command, output=None):
                process = subprocess.Popen(
                    command, cwd=ROOT, stdout=output or log, stderr=log, start_new_session=True
                )
                children.append(process)
                return process

            def consume():
                nonlocal measurement_count
                for name, offset in list(offsets.items()):
                    if name not in app_files:
                        continue
                    rows, offsets[name] = drain_jsonl(app_files[name], offset)
                    for row in rows:
                        app = row.get("application", name)
                        payload = {
                            **row,
                            "run_id": run_id,
                            "phase": workload_plan(
                                max(0, row["timestamp"] - document["measurement_started_at"])
                            )["phase"],
                        }
                        store.log(row["timestamp"], "application_measurement", payload)
                        document["application"][app] = payload
                        measurement_count += 1

            try:
                document.update(starting=True, updated_at=time.time())
                atomic_json(STATE, document)
                previous = http.get("/api/state").raise_for_status().json()
                http.post("/api/configuration", json={"mode": "manual"}).raise_for_status()
                wait_idle(http, lambda: stopping)
                if stopping:
                    raise RuntimeError("Stopped during workload setup")
                original, _ = adapter.client.preflight()
                adapter.client.readback(original)
                original_classification = adapter.client.managed_rule(original)["classification"]
                atomic_json(
                    ROOT / "lab/runtime/real-traffic-policy.json",
                    {
                        "run_id": run_id,
                        "classification": original_classification,
                        "mode": previous["mode"],
                        "profile": previous["profile"],
                    },
                )
                application_id = ensure_classifier(adapter)
                updated = policy_body(original)
                adapter.client.managed_rule(updated)["classification"] = {
                    "application": {
                        "appId": application_id,
                        "category": "",
                        "serviceClass": "",
                        "importance": "",
                    }
                }
                expected_classification = adapter.client.managed_rule(updated)["classification"]
                adapter.apply_experiment_policy(updated, "workload-start-" + uuid.uuid4().hex)
                changed_classifier = True
                if stopping:
                    raise RuntimeError("Stopped during workload setup")
                endpoints = create_endpoints()
                document.update(starting=True, updated_at=time.time(), endpoints=endpoints)
                atomic_json(STATE, document)
                for binding in endpoints:
                    receiver = child(
                        [
                            "ip",
                            "netns",
                            "exec",
                            binding["namespace"],
                            sys.executable,
                            "-m",
                            "lab.workload_guard",
                            "--parent",
                            str(os.getpid()),
                            "--kind",
                            "receiver",
                            "--link",
                            binding["link"],
                            "--side",
                            binding["side"],
                        ],
                        subprocess.DEVNULL,
                    )
                    receivers.append(receiver)
                    guards.append(receiver)
                wait_receivers(endpoints, receivers)
                update(capacity_only(), change=True)
                guards.append(
                    child(
                        [
                            "ip",
                            "netns",
                            "exec",
                            "sdw-server",
                            sys.executable,
                            "-m",
                            "lab.workload_guard",
                            "--parent",
                            str(os.getpid()),
                            "--kind",
                            "http",
                        ]
                    )
                )
                guards.append(
                    child(
                        [
                            "ip",
                            "netns",
                            "exec",
                            "sdw-server",
                            sys.executable,
                            "-m",
                            "lab.workload_guard",
                            "--parent",
                            str(os.getpid()),
                            "--kind",
                            "udp",
                        ]
                    )
                )
                for link in CAPACITY:
                    captures.append(
                        child(
                            [
                                "ip",
                                "netns",
                                "exec",
                                "sdw-" + link,
                                "tcpdump",
                                "-Z",
                                "root",
                                "-U",
                                "-s",
                                "160",
                                "-C",
                                "16",
                                "-W",
                                "3",
                                "-i",
                                "b",
                                "-w",
                                str(destination / (link + ".pcap")),
                                "ip proto 50 or udp port 4500 or udp port 4789",
                            ]
                        )
                    )
                subprocess.run(
                    [
                        "ip",
                        "netns",
                        "exec",
                        "sdw-client",
                        sys.executable,
                        "-m",
                        "lab.application_traffic",
                        "ready",
                    ],
                    cwd=ROOT,
                    check=True,
                    stdout=log,
                    stderr=log,
                    timeout=20,
                )
                if stopping:
                    raise RuntimeError("Stopped during workload setup")
                http.post(
                    "/api/configuration", json={"mode": previous["mode"], "profile": "voice"}
                ).raise_for_status()
                start = time.monotonic()
                document.update(
                    active=True,
                    starting=False,
                    measurement_started_at=time.time(),
                    updated_at=time.time(),
                )
                atomic_json(STATE, document)
                store.log(
                    time.time(),
                    "real_traffic_started",
                    {k: v for k, v in document.items() if k != "pid"},
                )
                app_cycle_started = -1000
                app_segment = -1
                http_worker = udp_worker = None
                while not stopping and (
                    not args.duration or time.monotonic() - start < args.duration
                ):
                    elapsed = time.monotonic() - start
                    if abs(time.time() - document["measurement_started_at"] - elapsed) > 1:
                        raise RuntimeError("Host clock changed; this workload run is incomplete")
                    if http_worker is None or (
                        http_worker.poll() is not None and udp_worker.poll() is not None
                    ):
                        if app_cycle_started >= 0:
                            if http_worker.returncode or udp_worker.returncode:
                                raise RuntimeError("Application segment failed; not restarted")
                            consume()
                            children.remove(http_worker)
                            children.remove(udp_worker)
                        app_cycle_started = elapsed
                        app_segment += 1
                        app_duration = (
                            min(600, max(1, int(args.duration - elapsed))) if args.duration else 600
                        )
                        app_files = {
                            name: destination / f"{name}-{app_segment}.jsonl" for name in offsets
                        }
                        offsets = dict.fromkeys(offsets, 0)
                        http_worker = child(
                            [
                                "ip",
                                "netns",
                                "exec",
                                "sdw-client",
                                sys.executable,
                                "-m",
                                "lab.application_traffic",
                                "client",
                                "--duration",
                                str(app_duration),
                                "--download-bytes",
                                "4194304",
                                "--download-interval",
                                "15",
                                "--output",
                                str(app_files["http"]),
                            ]
                        )
                        udp_worker = child(
                            [
                                "ip",
                                "netns",
                                "exec",
                                "sdw-client",
                                sys.executable,
                                "-m",
                                "lab.traffic",
                                "client",
                                "--duration",
                                str(int(app_duration)),
                                "--source-port",
                                "43009",
                                "--port",
                                "5209",
                                "--mbps",
                                "2",
                                "--output",
                                str(destination / f"udp-{app_segment}.json"),
                                "--interval-output",
                                str(app_files["udp"]),
                            ]
                        )
                    frame = workload_plan(elapsed)
                    if frame["phase"] != phase:
                        phase = frame["phase"]
                        store.log(time.time(), "real_traffic_phase", {"run_id": run_id, **frame})
                    if any(guard.poll() is not None for guard in guards):
                        raise RuntimeError("An owned workload server stopped")
                    if any(capture.poll() is not None for capture in captures):
                        raise RuntimeError("An application packet capture stopped")
                    for entry in list(loads):
                        process, handle, link, side, rate, issued_at, path, issued_phase = entry
                        if process.poll() is None:
                            if time.time() - issued_at > 25:
                                raise RuntimeError("A pinned background measurement timed out")
                            continue
                        handle.close()
                        result = parse_iperf_json(path.read_text(), requested_mbps=rate)
                        payload = {
                            **result,
                            "success": result["ok"],
                            "link_id": link,
                            "side": side,
                            "offered_mbps": result["sender_mbps"],
                            "run_id": run_id,
                            "phase": issued_phase,
                            "timestamp": issued_at,
                            "direction": "to_edge_" + side,
                        }
                        store.log(issued_at, "background_measurement", payload)
                        document["background"][link + "_" + side] = payload
                        loads.remove(entry)
                        children.remove(process)
                        if not result["ok"]:
                            raise RuntimeError("Receiver evidence unavailable for pinned WAN load")
                    if not loads and elapsed - background_at >= 10:
                        background_at = elapsed
                        # Bind the label to the rate plan actually launched, even
                        # if a phase boundary occurs while the six clients start.
                        load_frame = workload_plan(time.monotonic() - start)
                        for link, rate in load_frame["per_direction_requested_mbps"].items():
                            for side in ("a", "b"):
                                path = destination / (
                                    "background-"
                                    + str(int(elapsed))
                                    + "-"
                                    + link
                                    + "-"
                                    + side
                                    + ".json"
                                )
                                handle = path.open("wb")
                                process = child(
                                    background_client_command(link, side, rate, 10), handle
                                )
                                loads.append(
                                    (
                                        process,
                                        handle,
                                        link,
                                        side,
                                        rate,
                                        time.time(),
                                        path,
                                        load_frame["phase"],
                                    )
                                )
                    consume()
                    if http_worker.poll() not in (None, 0) or udp_worker.poll() not in (None, 0):
                        raise RuntimeError(
                            "Application workload failed; measurements were preserved"
                        )
                    api_state = http.get("/api/state").raise_for_status().json()
                    with (destination / "api-state.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "timestamp": time.time(),
                                    "elapsed": elapsed,
                                    "phase": phase,
                                    "state": api_state,
                                }
                            )
                            + "\n"
                        )
                    document.update(
                        updated_at=time.time(),
                        elapsed_seconds=round(elapsed, 2),
                        phase=phase,
                        cycle=frame["cycle"],
                        measurement_count=measurement_count,
                    )
                    atomic_json(STATE, document)
                    store.put("application_workload", document)
                    store.put("wan_environment", document)
                    deadline = time.monotonic() + 2
                    while not stopping and time.monotonic() < deadline:
                        time.sleep(0.1)
            except Exception as error:
                document["errors"].append(type(error).__name__ + ": " + str(error))
                raise
            finally:
                cleanup_errors = stop_processes(children, captures, guards)
                for _, handle, *_ in loads:
                    handle.close()
                try:
                    remove_endpoints(endpoints)
                except Exception as error:  # noqa: BLE001 - still attempt qdisc/policy restoration
                    cleanup_errors.append("Receiver topology cleanup: " + type(error).__name__)
                try:
                    if "measurement_started_at" in document:
                        consume()
                except Exception as error:  # noqa: BLE001 - cleanup must still restore network/policy
                    cleanup_errors.append("Measurement cleanup: " + type(error).__name__)
                try:
                    update(impairment_plan("CONTROL"), change=True)
                except Exception as error:  # noqa: BLE001 - record failure and attempt policy cleanup
                    cleanup_errors.append("Network cleanup: " + type(error).__name__)
                try:
                    if previous:
                        http.post("/api/configuration", json={"mode": "manual"}).raise_for_status()
                        wait_idle(http)
                        if adapter.journal.unresolved():
                            raise RuntimeError(
                                "Unresolved policy write; mode stays Manual until reconciliation"
                            )
                        if changed_classifier:
                            current, _ = adapter.client.preflight()
                            restored = policy_body(current)
                            rule = adapter.client.managed_rule(restored)
                            if rule["classification"] != expected_classification:
                                raise RuntimeError("Classifier changed concurrently; preserved")
                            rule["classification"] = original_classification
                            adapter.apply_experiment_policy(
                                restored, "workload-stop-" + uuid.uuid4().hex
                            )
                        http.post(
                            "/api/configuration",
                            json={"mode": previous["mode"], "profile": previous["profile"]},
                        ).raise_for_status()
                except Exception as error:  # noqa: BLE001 - uncertain cleanup stays explicit
                    cleanup_errors.append("Policy cleanup: " + type(error).__name__)
                document.update(
                    active=False,
                    starting=False,
                    updated_at=time.time(),
                    cleanup_errors=cleanup_errors,
                    restored_baseline=not cleanup_errors,
                    measurement_count=measurement_count,
                    completed=not document["errors"] and not cleanup_errors,
                    receiver_coverage=len(document["background"]),
                    endpoint_coverage=list(document["application"]),
                )
                atomic_json(STATE, document)
                atomic_json(destination / "summary.json", document)
                store.put("application_workload", document)
                store.put("wan_environment", document)
                store.log(
                    time.time(),
                    "real_traffic_stopped",
                    {k: v for k, v in document.items() if k != "pid"},
                )
                adapter.client.close()
                if cleanup_errors:
                    raise RuntimeError("Workload cleanup incomplete; inspect runtime state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "start", "status", "stop"))
    parser.add_argument(
        "--duration", type=int, default=210, help="Seconds; 0 repeats until stopped"
    )
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--stream")
    args = parser.parse_args()
    if args.duration < 0 or args.duration > 7200 or 0 < args.duration < 15:
        parser.error("Duration must be 0 or 15..7200 seconds")
    if args.action == "plan":
        print(
            json.dumps(
                {
                    "capacity_only": capacity_only(),
                    "cycle_seconds": CYCLE_SECONDS,
                    "phases": [workload_plan(t) for t in (0, 60, 150)],
                    "real_isp": False,
                    "receiver_measured": True,
                },
                indent=2,
            )
        )
        return
    require_owned()
    if args.action == "status":
        print(STATE.read_text() if STATE.exists() else '{"active": false}')
    elif args.action == "stop":
        if not STATE.exists():
            print("Real traffic mode has not been started")
            return
        document = json.loads(STATE.read_text())
        process = Path("/proc") / str(document["pid"]) / "cmdline"
        if (document.get("active") or document.get("starting")) and process.exists():
            command = process.read_bytes().split(b"\0")
            if b"lab.real_traffic" not in command or b"start" not in command:
                raise RuntimeError("Saved PID belongs to another process; preserved")
            os.kill(document["pid"], signal.SIGTERM)
            print("Stop requested; check status for verified restoration")
        elif document.get("active") or document.get("starting"):
            raise RuntimeError(
                "Runner exited without cleanup; reconcile policy before restoring WANs"
            )
        else:
            print("Real traffic mode is stopped")
    elif args.background:
        with shaping_session():
            inspect_baseline()
            store = live_store(args.stream)
        path = ROOT / "lab/runtime/logs/real-traffic.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "lab.real_traffic",
                    "start",
                    "--duration",
                    str(args.duration),
                    "--stream",
                    store.stream,
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        print("Real traffic starting: PID " + str(process.pid) + "; stream " + store.stream)
    else:
        run(args)


if __name__ == "__main__":
    main()

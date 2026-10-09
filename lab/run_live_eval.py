"""Real netem + UDP + PCAP evaluation. Requires provisioned flexiEdge lab.

All outputs are live observations. No synthetic replay, no fabricated trial result.
Use --plan on Windows to review the full experiment without changing networks.
"""

import argparse
import base64
import hashlib
import json
import os
import random
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from lab.baselines import PROFILE_PORT, ensure_application, experiment_policy
from lab.impairments import apply, plan, require_linux
from sdwan.adapters import FlexiWANAdapter
from sdwan.domain import PROFILES
from sdwan.flexiwan import FlexiWANConfig
from sdwan.scenarios import SUITES, manifest, suite_cases
from sdwan.scenarios import scenario as resolve_scenario

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SECONDS = 70  # At least 12 fresh five-second frames before injecting faults.
FAULT_SECONDS = 80
RESTORE_SECONDS = 20
EVENT_END = BASELINE_SECONDS + FAULT_SECONDS
TRIAL_SECONDS = EVENT_END + RESTORE_SECONDS


def write_json(path, value):
    """Checkpoint an artifact without exposing a partially written JSON file."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_elapsed_clocks(wall_start, monotonic_start, previous_elapsed):
    elapsed = time.monotonic() - monotonic_start
    if abs((time.time() - wall_start) - elapsed) > 1 or elapsed - previous_elapsed > 12:
        raise RuntimeError("Host suspend or clock discontinuity invalidated this packet trial")
    return elapsed


def packet_evidence(directory):
    """Identify observed artifacts; starting a process alone is not packet evidence."""
    directory = Path(directory)
    evidence = {"evidence_level": "live_lab_attempt", "packet_artifacts": []}
    try:
        document = json.loads((directory / "packets.json").read_text(encoding="utf-8"))
        intervals = document["intervals"]
        if (
            document.get("target") != "10.30.0.2"
            or document.get("packet_bytes") != 1250
            or not isinstance(intervals, list)
            or not intervals
            or any(
                not isinstance(row, dict)
                or row.get("measurement") != "udp_echo_rtt_stddev"
                or type(row.get("sent_packets")) is not int
                or type(row.get("received_packets")) is not int
                or not 0 <= row["received_packets"] <= row["sent_packets"]
                for row in intervals
            )
            or sum(row["sent_packets"] for row in intervals) == 0
        ):
            return evidence
        captures = []
        byte_order = {
            b"\xd4\xc3\xb2\xa1": "<",
            b"\xa1\xb2\xc3\xd4": ">",
            b"\x4d\x3c\xb2\xa1": "<",
            b"\xa1\xb2\x3c\x4d": ">",
        }
        for link in ("wan1", "wan2", "wan3"):
            path = directory / (link + ".pcap")
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                header = stream.read(40)
                if len(header) != 40 or header[:4] not in byte_order:
                    continue
                captured, wire = struct.unpack(byte_order[header[:4]] + "II", header[32:40])
                if 0 < captured <= 160 and wire > 1000 and len(stream.read(captured)) == captured:
                    captures.append(path.name)
        if captures:
            evidence.update(
                evidence_level="real_packets",
                packet_artifacts=["packets.json", *captures],
                observed_packet_intervals=len(intervals),
            )
    except (OSError, ValueError, KeyError, TypeError):
        # A missing/truncated artifact must not upgrade a failed setup to packet evidence.
        pass
    return evidence


def save_results(destination, results, complete, order_seed, case_manifest=None):
    observed = [row for row in results if row.get("evidence_level") == "real_packets"]
    write_json(
        destination / "results.json",
        {
            "evidence_level": "real_packets" if observed else "live_lab_attempt",
            "complete": complete,
            "order_seed": order_seed,
            "trials": results,
            "packet_observed_trials": len(observed),
            "native_qbr_evaluated": any(row["strategy"] == "native_qbr" for row in observed),
            **({"case_manifest": case_manifest} if case_manifest else {}),
        },
    )


def assess(packets, states, scenario, profile):
    event_start, event_end, grace = BASELINE_SECONDS, EVENT_END, 10
    rows = [row for row in packets if event_start + grace <= row["second"] < event_end]
    if not rows or any(row["offered_mbps"] < profile.demand_mbps * 0.90 for row in rows):
        return {
            "valid": False,
            "passed": False,
            "reason": "Traffic generator underdelivered; harness limitation",
        }

    def good(row):
        return (
            row["received_packets"] > 0
            and row["loss_pct"] <= profile.loss_pct
            and row["latency_ms"] <= profile.latency_ms
            and row["jitter_ms"] <= profile.jitter_ms
            and row["received_mbps"] >= row["offered_mbps"] * 0.95
        )

    baseline = [row for row in packets if 10 <= row["second"] < event_start]
    if (
        not baseline
        or any(row["offered_mbps"] < profile.demand_mbps * 0.90 for row in baseline)
        or sum(good(row) for row in baseline) / len(baseline) < 0.98
    ):
        return {"valid": False, "passed": False, "reason": "Healthy baseline was not established"}
    if scenario == "S07":
        no_path = [
            row["second"]
            for row in states
            if row["second"] >= event_start
            and (row["state"].get("last_decision") or {}).get("action") == "no_path"
        ]
        detection = min(no_path) - event_start if no_path else None
        return {
            "valid": True,
            "passed": all(row["received_packets"] == 0 for row in rows)
            and detection is not None
            and detection <= 5,
            "restored": False,
            "no_path_detection_seconds": detection,
            "packet_delivery_expected": False,
        }

    successes = sum(good(row) for row in rows)
    recovery = None
    for index in range(event_start, len(packets) - 9):
        if all(good(row) for row in packets[index : index + 10]):
            recovery = index - event_start
            break
    ratio = successes / len(rows)
    return {
        "valid": True,
        "passed": ratio >= 0.98 and recovery is not None and recovery <= 15,
        "restored": ratio >= 0.98,
        "sla_interval_ratio": ratio,
        "recovery_seconds": recovery,
        "udp_rtt_is_latency_proxy": True,
        "udp_rtt_stddev_is_jitter_proxy": True,
    }


def edge_fault(ssh_key, mode, links=("wan1",)):
    if (
        mode not in {"none", "missing", "corrupt"}
        or not links
        or not set(links) <= {"wan1", "wan2", "wan3"}
    ):
        raise ValueError("Invalid scoped telemetry fault")
    payload = base64.b64encode(
        json.dumps({"mode": mode, "links": list(links), "created_at": time.time()}).encode()
    ).decode()
    # Only fixed lab IP/path and base64 JSON enter the remote shell.
    code = (
        "import base64,pathlib; p=pathlib.Path('/run/ai-sdwan'); p.mkdir(exist_ok=True); (p/'lab-fault.json').write_bytes(base64.b64decode('"
        + payload
        + "'))"
    )
    subprocess.run(
        [
            "ssh",
            "-i",
            ssh_key,
            "-o",
            "BatchMode=yes",
            "-o",
            "UserKnownHostsFile=" + str(ROOT / "lab/runtime/known_hosts"),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "lab@192.168.56.11",
            "sudo python3 -c " + "'" + code.replace("'", "'\"'\"'") + "'",
        ],
        check=True,
        timeout=20,
    )


def saturation_command(injection, destination):
    link = injection["background_load_link"]
    if link not in {"wan1", "wan2", "wan3"} or injection["background_load_mbps"] <= 0:
        raise ValueError("A saturation command requires a scoped load target")
    return [
        "ip",
        "netns",
        "exec",
        "sdw-" + link,
        sys.executable,
        str(ROOT / "lab/traffic.py"),
        "flood",
        "--host",
        f"172.16.{int(link[-1])}.1",
        "--port",
        "9999",
        "--mbps",
        str(injection["background_load_mbps"]),
        "--duration",
        str(FAULT_SECONDS),
        "--output",
        str(Path(destination) / "underlay-load.json"),
    ]


def child(arguments, log):
    return subprocess.Popen(arguments, cwd=ROOT, stdout=log, stderr=log, start_new_session=True)


def terminate(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def trial(
    configuration,
    config_path,
    ssh_key,
    scenario,
    profile_name,
    strategy,
    destination,
    source_port=0,
):
    destination = Path(destination)
    # Refuse reuse before opening a controller client or submitting any mutation.
    destination.mkdir(parents=True, exist_ok=False)
    progress = {
        "phase": "controller_preflight",
        "packet_measurement_started": False,
        "packet_measurement_completed": False,
    }
    states, cleanup_errors = [], []
    adapter = None
    result = None
    write_json(destination / "trial.json", {**progress, "status": "running"})
    try:
        adapter = FlexiWANAdapter(configuration)
        result = run_trial(
            adapter,
            config_path,
            ssh_key,
            scenario,
            profile_name,
            strategy,
            destination,
            states,
            progress,
            cleanup_errors,
            source_port,
        )
        return {
            **result,
            **packet_evidence(destination),
            "packet_measurement_completed": progress["packet_measurement_completed"],
        }
    finally:
        # sys.exception() also preserves interruption/cancellation without catching BaseException.
        primary_error = sys.exception()
        if adapter is not None:
            try:
                adapter.client.close()
            except Exception as error:  # noqa: BLE001 - cleanup must preserve the primary error
                cleanup_errors.append(
                    {"action": "close_controller", "error_type": type(error).__name__}
                )
        artifacts = [
            (destination / "states.json", states),
            (
                destination / "trial.json",
                {
                    **progress,
                    **packet_evidence(destination),
                    "status": "failed" if primary_error or cleanup_errors else "completed",
                    "error_type": type(primary_error).__name__ if primary_error else None,
                    "cleanup_errors": cleanup_errors,
                    "state_observations": len(states),
                    "result": result,
                },
            ),
        ]
        persistence_error = None
        for path, document in artifacts:
            try:
                write_json(path, document)
            except Exception as error:  # noqa: BLE001 - report persistence must not mask a trial failure
                if primary_error is not None:
                    primary_error.add_note(
                        "Could not save " + path.name + ": " + type(error).__name__
                    )
                elif persistence_error is None:
                    persistence_error = error
        if primary_error is None:
            if persistence_error is not None:
                raise persistence_error
            if cleanup_errors:
                raise RuntimeError("Lab cleanup failed; inspect trial.json")


def copy_audit(adapter, destination):
    metadata = destination / "database-stream.json"
    if not metadata.exists():
        return
    from sdwan.storage import Store

    stream = json.loads(metadata.read_text())["stream"]
    snapshot = Store(stream=stream).export()
    (destination / "audit.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    journal_ids = [
        row["payload"]["id"] for row in snapshot["events"] if row["kind"] == "apply_intent"
    ]
    for identifier in journal_ids:
        path = adapter.journal.file(identifier)
        if path.exists():
            folder = destination / "journal"
            folder.mkdir(exist_ok=True)
            (folder / path.name).write_bytes(path.read_bytes())


def run_trial(
    adapter,
    config_path,
    ssh_key,
    scenario,
    profile_name,
    strategy,
    destination,
    states,
    progress,
    cleanup_errors,
    source_port=0,
):
    profile = PROFILES[profile_name]
    case = resolve_scenario(scenario)
    original, _ = adapter.client.preflight()
    adapter.client.readback(original)
    progress["phase"] = "application_setup"
    application_id = ensure_application(adapter.client, profile_name)
    updated = experiment_policy(adapter.client, original, profile_name, strategy, application_id)
    baseline_id = "trial-baseline-" + uuid.uuid4().hex
    progress["phase"] = "controller_apply"
    progress["baseline_journal_id"] = baseline_id
    try:
        adapter.apply_experiment_policy(updated, baseline_id)
    finally:
        primary_error = sys.exception()
        try:
            intent = adapter.journal.file(baseline_id)
            if intent.exists():
                (destination / "baseline-policy.json").write_bytes(intent.read_bytes())
        except Exception as error:
            if primary_error is None:
                raise
            primary_error.add_note("Could not save baseline journal: " + type(error).__name__)
    packet_file = destination / "packets.json"
    service = echo = traffic = None
    captures = []
    headers = (
        {"Authorization": "Bearer " + os.environ["SDWAN_API_TOKEN"]}
        if os.getenv("SDWAN_API_TOKEN")
        else {}
    )
    with (destination / "processes.log").open("wb") as log:
        try:
            progress["phase"] = "network_reset"
            apply(plan("CONTROL"))
            edge_fault(ssh_key, "none")
            command = [
                sys.executable,
                "-m",
                "sdwan",
                "serve",
                "--port",
                "8001",
                "--flexiwan-config",
                str(config_path),
                "--stream",
                "trial-" + uuid.uuid4().hex,
            ]
            (destination / "database-stream.json").write_text(
                json.dumps({"storage": "timescaledb", "stream": command[-1]}) + "\n",
                encoding="utf-8",
            )
            if strategy == "predictive_synthetic_lab":
                command += ["--experimental-synthetic-forecast"]
            if strategy == "native_qbr":
                command += ["--observe-native-qbr"]
            progress["phase"] = "service_start"
            service = child(command, log)
            import ssl

            with httpx.Client(
                base_url="https://localhost:8001",
                headers=headers,
                timeout=1,
                trust_env=False,
                verify=ssl.create_default_context(cafile=os.getenv("SDWAN_CA_FILE")),
            ) as http:
                for _ in range(45):
                    if service.poll() is not None:
                        raise RuntimeError(
                            "Live service failed to start; inspect trial processes.log"
                        )
                    try:
                        health = http.get("/health")
                        if health.status_code == 200 and health.json()["backend"].startswith(
                            "flexiwan"
                        ):
                            if (
                                strategy == "predictive_synthetic_lab"
                                and not health.json()["model_loaded"]
                            ):
                                raise RuntimeError(
                                    "Forecast trial requires the frozen model; reactive fallback is not AI evidence"
                                )
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("Live service readiness timeout")
                progress["phase"] = "service_configuration"
                http.post(
                    "/api/configuration",
                    json={
                        "mode": "manual" if strategy.startswith("native_") else "auto",
                        "profile": profile_name,
                    },
                ).raise_for_status()
                progress["phase"] = "capture_start"
                echo = child(
                    [
                        "ip",
                        "netns",
                        "exec",
                        "sdw-server",
                        sys.executable,
                        str(ROOT / "lab/traffic.py"),
                        "server",
                        "--port",
                        str(PROFILE_PORT[profile_name]),
                    ],
                    log,
                )
                for link in ("wan1", "wan2", "wan3"):
                    captures.append(
                        child(
                            [
                                "ip",
                                "netns",
                                "exec",
                                "sdw-" + link,
                                "tcpdump",
                                "-U",
                                "-s",
                                "160",
                                "-i",
                                "b",
                                "-w",
                                str(destination / (link + ".pcap")),
                                "greater",
                                "1000",
                                "and",
                                "not",
                                "udp",
                                "port",
                                "9999",
                            ],
                            log,
                        )
                    )
                time.sleep(1)
                progress["phase"] = "packet_measurement"
                traffic = child(
                    [
                        "ip",
                        "netns",
                        "exec",
                        "sdw-client",
                        sys.executable,
                        str(ROOT / "lab/traffic.py"),
                        "client",
                        "--port",
                        str(PROFILE_PORT[profile_name]),
                        "--source-port",
                        str(source_port),
                        "--duration",
                        str(TRIAL_SECONDS),
                        "--mbps",
                        str(profile.demand_mbps),
                        "--output",
                        str(packet_file),
                    ],
                    log,
                )
                progress["packet_measurement_started"] = True
                start = time.monotonic()
                wall_start = time.time()
                previous_elapsed = 0
                fault_set = False
                background = None
                try:
                    while True:
                        try:
                            elapsed = verify_elapsed_clocks(wall_start, start, previous_elapsed)
                        except RuntimeError:
                            progress["phase"] = "host_clock_discontinuity"
                            raise
                        previous_elapsed = elapsed
                        if elapsed >= TRIAL_SECONDS:
                            break
                        active = BASELINE_SECONDS <= elapsed < EVENT_END
                        if active and (not fault_set or case.kind in {"ramp", "flap"}):
                            if not fault_set and strategy == "predictive_synthetic_lab":
                                forecasts = (
                                    (states[-1]["state"].get("last_decision") or {}).get(
                                        "predicted", {}
                                    )
                                    if states
                                    else {}
                                )
                                if set(forecasts) != {"wan1", "wan2", "wan3"}:
                                    raise RuntimeError(
                                        "Forecast did not warm up on all three WANs before fault injection"
                                    )
                            progress["phase"] = "fault_injection"
                            apply(plan(scenario, elapsed - BASELINE_SECONDS))
                            if not fault_set:
                                injection = plan(scenario)
                                edge_fault(
                                    ssh_key,
                                    injection["telemetry_fault"] or "none",
                                    injection["telemetry_links"] or ("wan1",),
                                )
                                if case.kind == "saturation":
                                    background = child(
                                        saturation_command(injection, destination),
                                        log,
                                    )
                                fault_set = True
                        elif not active and fault_set:
                            progress["phase"] = "fault_restoration"
                            apply(plan("CONTROL"))
                            edge_fault(ssh_key, "none")
                            fault_set = False
                            terminate(background)
                        progress["phase"] = "packet_measurement"
                        try:
                            reply = http.get("/api/state")
                            reply.raise_for_status()
                            states.append(
                                {"second": time.monotonic() - start, "state": reply.json()}
                            )
                        except httpx.HTTPError:
                            states.append(
                                {
                                    "second": time.monotonic() - start,
                                    "state": {},
                                    "control_api_unavailable": True,
                                }
                            )
                        write_json(destination / "states.json", states)
                        if any(capture.poll() is not None for capture in captures):
                            raise RuntimeError(
                                "PCAP capture failed; trial cannot claim forwarding evidence"
                            )
                        time.sleep(max(0, 5 - (time.monotonic() - start - elapsed)))
                    traffic.wait(timeout=15)
                    if traffic.returncode != 0:
                        raise RuntimeError("UDP traffic measurement failed")
                finally:
                    try:
                        terminate(background)
                    except Exception as error:  # noqa: BLE001 - finish the remaining cleanup actions
                        cleanup_errors.append(
                            {"action": "terminate_background", "error_type": type(error).__name__}
                        )
        finally:
            primary_error = sys.exception()
            for process in [service, traffic, echo, *captures]:
                try:
                    terminate(process)
                except Exception as error:  # noqa: BLE001 - finish the remaining cleanup actions
                    cleanup_errors.append(
                        {"action": "terminate_child", "error_type": type(error).__name__}
                    )
            actions = (
                ("reset_network", lambda: apply(plan("CONTROL"))),
                ("reset_telemetry", lambda: edge_fault(ssh_key, "none")),
                ("copy_audit", lambda: copy_audit(adapter, destination)),
            )
            for name, action in actions:
                try:
                    action()
                except Exception as error:  # noqa: BLE001 - preserve the primary trial error
                    cleanup_errors.append({"action": name, "error_type": type(error).__name__})
            if cleanup_errors and primary_error is None:
                raise RuntimeError("Lab cleanup failed; inspect trial.json")
    progress["phase"] = "assessment"
    packets = json.loads(packet_file.read_text(encoding="utf-8"))["intervals"]
    if packet_evidence(destination)["evidence_level"] != "real_packets":
        raise RuntimeError("No observed UDP/PCAP artifacts; trial cannot claim packet evidence")
    progress["packet_measurement_completed"] = True
    result = assess(packets, states, scenario, profile)
    if case.kind == "saturation":
        observed = [
            sample.get("utilization_pct", 0)
            for state in states
            if BASELINE_SECONDS <= state["second"] < EVENT_END
            for link, sample in state["state"].get("telemetry", {}).items()
            if link in case.affected_links
        ]
        if not observed or max(observed) < 95:
            result.update(
                valid=False,
                passed=False,
                reason=case.affected_links[0].upper()
                + " saturation was not achieved; harness limitation",
            )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/flexiwan.local.json")
    parser.add_argument("--ssh-key")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--order-seed", type=int, default=91001)
    parser.add_argument("--source-port-base", type=int, default=42000)
    parser.add_argument("--routing-evidence", help="Fresh read-only OSPF timer verification JSON")
    parser.add_argument("--clock-evidence", help="Fresh guest/host clock verification JSON")
    parser.add_argument("--suite", choices=SUITES, default="balanced")
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--profiles", nargs="+", choices=list(PROFILES), default=list(PROFILES))
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["native_priority", "native_qbr", "reactive", "predictive_synthetic_lab"],
        default=["native_priority", "native_qbr", "reactive", "predictive_synthetic_lab"],
    )
    parser.add_argument("--output", default="reports/live")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.scenarios is None:
        args.scenarios = [case.id for case in suite_cases(args.suite, legacy_control=False)]
    if not 1 <= args.repetitions <= 100:
        parser.error("repetitions must be 1..100")
    if not 1024 <= args.source_port_base <= 65535 - args.repetitions + 1:
        parser.error("source ports must fit in 1024..65535")
    for field in ("scenarios", "profiles", "strategies"):
        values = getattr(args, field)
        if len(values) != len(set(values)):
            parser.error("Duplicate " + field + " would reuse a trial directory")
    try:
        catalogue = manifest(args.suite, args.scenarios, legacy_control="CONTROL" in args.scenarios)
    except ValueError as error:
        parser.error(str(error))
    for scenario in args.scenarios:
        plan(scenario)
    cases = [
        (scenario, profile, strategy, repeat)
        for repeat in range(args.repetitions)
        for scenario in args.scenarios
        for profile in args.profiles
        for strategy in args.strategies
    ]
    random.Random(args.order_seed).shuffle(cases)
    if args.plan:
        print(
            json.dumps(
                {
                    "case_manifest": {
                        key: value for key, value in catalogue.items() if key != "cases"
                    },
                    "selected_cases": args.scenarios,
                    "trials": len(cases),
                    "minimum_minutes": len(cases) * TRIAL_SECONDS / 60,
                    "minimum_hours": len(cases) * TRIAL_SECONDS / 3600,
                    "seconds_per_trial": TRIAL_SECONDS,
                    "baseline_seconds": BASELINE_SECONDS,
                    "flow_source_ports": [
                        args.source_port_base + i for i in range(args.repetitions)
                    ],
                    "order_seed": args.order_seed,
                    "native_qbr": "Same explicit UDP application classifier; AI observes without policy writes",
                    "measurement": "actual UDP echo + per-WAN PCAP",
                    "cases": cases[:6],
                },
                indent=2,
            )
        )
        return
    require_linux()
    if not args.ssh_key:
        parser.error("--ssh-key required for controlled telemetry faults")
    if not (ROOT / "lab/runtime/known_hosts").is_file():
        parser.error(
            "Connect to both lab edges using lab/runtime/known_hosts before running trials"
        )
    for port in (8000, 8001):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(
                    f"Port {port} is in use; stop the dashboard/runner before the experiment"
                )
    config = FlexiWANConfig.load(args.config)
    routing_evidence = None
    clock_evidence = None
    if args.routing_evidence:
        routing_evidence = json.loads(Path(args.routing_evidence).read_text(encoding="utf-8"))
        if (
            routing_evidence.get("evidence_level") != "real_edge_observations"
            or not 0 <= time.time() - routing_evidence.get("observed_at", 0) <= 60
        ):
            raise ValueError("Routing verification must be observed within the last 60 seconds")
    if args.clock_evidence:
        clock_evidence = json.loads(Path(args.clock_evidence).read_text(encoding="utf-8"))
        if (
            clock_evidence.get("evidence_level") != "real_edge_clock_observations"
            or not 0 <= time.time() - clock_evidence.get("observed_at", 0) <= 60
            or len(clock_evidence.get("edges", [])) != 2
            or any(
                abs(row["after_offset_upper_bound_seconds"]) > 1 for row in clock_evidence["edges"]
            )
        ):
            raise ValueError("Both guest clocks must be verified within one second of the host")
    destination = Path(args.output).resolve()
    if destination.exists():
        raise RuntimeError("Existing live output was preserved; choose a new --output")
    destination.mkdir(parents=True, exist_ok=False)
    metadata = ROOT / "artifacts/model/metadata.json"
    (destination / "experiment.json").write_text(
        json.dumps(
            {
                "evidence_level": "live_lab_plan",
                "case_manifest": catalogue,
                "created_at": time.time(),
                "configuration": config.model_dump(),
                "strategies": args.strategies,
                "profiles": {name: vars(PROFILES[name]) for name in args.profiles},
                "baseline_seconds": BASELINE_SECONDS,
                "fault_seconds": FAULT_SECONDS,
                "restore_seconds": RESTORE_SECONDS,
                "grace_seconds": 10,
                "repetitions": args.repetitions,
                "order_seed": args.order_seed,
                "flow_source_ports": [args.source_port_base + i for i in range(args.repetitions)],
                "flow_assignment": "Same fixed UDP source port for each repetition across strategies/cases",
                "forecast_sampling": "causal_linear_5s; measured intervals 4..6s; no gap/down filling or extrapolation",
                "routing_evidence": routing_evidence,
                "clock_evidence": clock_evidence,
                "model_metadata": json.loads(metadata.read_text(encoding="utf-8"))
                if metadata.exists()
                else None,
                "model_files_sha256": {
                    file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in sorted(metadata.parent.glob("*.json"))
                },
                "source_files_sha256": {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in (
                        "lab/run_live_eval.py",
                        "lab/impairments.py",
                        "lab/traffic.py",
                        "lab/edge/probe.py",
                        "lab/edge/ospf.py",
                        "lab/edge/clock.py",
                        "lab/baselines.py",
                        "sdwan/model.py",
                        "sdwan/features.py",
                        "sdwan/domain.py",
                        "sdwan/scenarios.py",
                        "sdwan/collector.py",
                        "sdwan/service.py",
                        "sdwan/api.py",
                        "sdwan/storage.py",
                        "sdwan/policy.py",
                        "sdwan/adapters.py",
                        "sdwan/flexiwan.py",
                        "scripts/run_live_lab.py",
                        "scripts/sync_edge_clocks.py",
                        "scripts/configure_lab_ospf.py",
                    )
                    if (ROOT / name).is_file()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    results = []
    for scenario, profile, strategy, repeat in cases:
        row = {
            "scenario": scenario,
            "group": resolve_scenario(scenario).group,
            "affected_links": list(resolve_scenario(scenario).affected_links),
            "profile": profile,
            "strategy": strategy,
            "repeat": repeat,
            "packet_measurement_completed": False,
        }
        folder = destination / f"{scenario}-{profile}-{strategy}-{repeat:03d}"
        try:
            row.update(
                trial(
                    config,
                    Path(args.config).resolve(),
                    args.ssh_key,
                    scenario,
                    profile,
                    strategy,
                    folder,
                    args.source_port_base + repeat,
                )
            )
        except Exception as error:
            row.update(
                packet_evidence(folder),
                valid=False,
                passed=False,
                error_type=type(error).__name__,
            )
            # trial.json captures the last phase and cleanup errors even when setup failed.
            try:
                detail = json.loads((folder / "trial.json").read_text(encoding="utf-8"))
                row.update(
                    phase=detail.get("phase"),
                    packet_measurement_started=detail.get("packet_measurement_started", False),
                    packet_measurement_completed=detail.get("packet_measurement_completed", False),
                    cleanup_errors=detail.get("cleanup_errors", []),
                )
            except Exception as artifact_error:  # noqa: BLE001 - retain the primary error and failed artifact read
                row["artifact_read_error_type"] = type(artifact_error).__name__
            results.append(row)
            try:
                save_results(destination, results, False, args.order_seed, catalogue)
            except Exception as persistence_error:  # noqa: BLE001 - preserve the primary trial error
                error.add_note("Could not save results.json: " + type(persistence_error).__name__)
            raise  # Stop instead of continuing after an uncertain controller mutation.
        results.append(row)
        save_results(destination, results, len(results) == len(cases), args.order_seed, catalogue)
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()

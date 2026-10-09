"""Run owned real packet experiments with private credentials and read-only diagnostics."""

import argparse
import concurrent.futures
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from sdwan.scenarios import CATALOGUE, SUITES, manifest

root = Path(__file__).resolve().parents[1]
if os.name != "posix" or not (root / ".ai-sdwan-owned").is_file():
    raise RuntimeError("Use the prepared owned Linux lab through WSL")
parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--suite", choices=SUITES, default="balanced")
parser.add_argument("--scenarios", nargs="+", choices=list(CATALOGUE), default=["S01"])
parser.add_argument(
    "--profiles", nargs="+", choices=["voice", "interactive", "bulk"], default=["voice"]
)
parser.add_argument(
    "--strategies",
    nargs="+",
    choices=["native_priority", "native_qbr", "reactive", "predictive_synthetic_lab"],
    default=["native_priority", "native_qbr", "reactive", "predictive_synthetic_lab"],
)
parser.add_argument("--repetitions", type=int, default=3)
parser.add_argument("--order-seed", type=int, default=91002)
args = parser.parse_args()
try:
    manifest(args.suite, args.scenarios, legacy_control="CONTROL" in args.scenarios)
except ValueError as error:
    parser.error(str(error))
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,100}", args.output) or not 1 <= args.repetitions <= 100:
    parser.error("Use a fresh simple experiment name and repetitions 1..100")
destination = root / "reports" / args.output
if destination.exists():
    raise RuntimeError("Existing experiment was preserved; choose a fresh output")
os.chdir(root)
clock_file = root / "reports/flexiwan" / (args.output + "-clock.json")
subprocess.run(
    [sys.executable, "scripts/sync_edge_clocks.py", "--apply", "--output", str(clock_file)],
    cwd=root,
    check=True,
)
routing_file = root / "reports/flexiwan" / (args.output + "-routing.json")
subprocess.run(
    [sys.executable, "scripts/configure_lab_ospf.py", "--output", str(routing_file)],
    cwd=root,
    check=True,
    stdout=subprocess.DEVNULL,
)
credentials = json.loads((root / "lab/runtime/credentials.json").read_text())
stack = json.loads((root / "lab/runtime/observability/private.json").read_text())
environment = {
    **os.environ,
    "FLEXIWAN_ACCESS_KEY": credentials["access_key"],
    "SDWAN_DATABASE_URL": stack["database_url"],
    "SDWAN_API_TOKEN": stack["api_token"],
    "SDWAN_TLS_CERT": str(root / "lab/runtime/observability/certs/api/server.crt"),
    "SDWAN_TLS_KEY": str(root / "lab/runtime/observability/certs/api/server.key"),
    "SDWAN_CA_FILE": str(root / "lab/runtime/certs/certificate.pem"),
}
options = [
    "-i",
    str(root / "lab/runtime/edge-key"),
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "UserKnownHostsFile=" + str(root / "lab/runtime/known_hosts"),
    "-o",
    "ConnectTimeout=5",
]
code = r"""
import json, subprocess, time
commands = [
    ['vppctl','show','fwabf','link'],
    ['vppctl','show','fwabf','policy'],
    ['ip','-json','route','show','10.20.0.0/24'],
    ['ip','-json','route','show','10.30.0.0/24'],
    ['vtysh','-c','show ip ospf neighbor json'],
    ['vtysh','-c','show ip ospf interface json'],
]
output = {'observed_at':time.time(), 'commands':[]}
for command in commands:
    result = subprocess.run(command,capture_output=True,text=True,timeout=2,restore_signals=False)
    output['commands'].append({'command':command,'returncode':result.returncode,'output':result.stdout})
print(json.dumps(output))
"""


def observe(address):
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["ssh", *options, "lab@" + address, "sudo python3 -"],
            input=code,
            text=True,
            capture_output=True,
            timeout=8,
            check=True,
        )
        return {**json.loads(result.stdout), "query_seconds": time.monotonic() - started}
    except (subprocess.SubprocessError, ValueError) as error:
        return {"observed_at": time.time(), "error_type": type(error).__name__}


log_path = root / "lab/runtime/logs" / (args.output + "-supervisor.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("ab") as log:
    runner = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lab.run_live_eval",
            "--suite",
            args.suite,
            "--config",
            "config/flexiwan.local.json",
            "--ssh-key",
            "lab/runtime/edge-key",
            "--scenarios",
            *args.scenarios,
            "--profiles",
            *args.profiles,
            "--strategies",
            *args.strategies,
            "--repetitions",
            str(args.repetitions),
            "--order-seed",
            str(args.order_seed),
            "--routing-evidence",
            str(routing_file),
            "--clock-evidence",
            str(clock_file),
            "--output",
            str(destination),
        ],
        cwd=root,
        env=environment,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    print(
        f"Actual packet comparison running: {len(args.scenarios) * len(args.profiles) * len(args.strategies) * args.repetitions} trials, runner PID {runner.pid}, {destination.name}",
        flush=True,
    )
    observations, seen = {}, {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            while runner.poll() is None:
                for file in sorted(destination.glob("S*/states.json")):
                    trial = file.parent
                    try:
                        detail = json.loads((trial / "trial.json").read_text())
                        states = json.loads(file.read_text())
                    except (FileNotFoundError, ValueError):
                        continue
                    if detail.get("status") != "running" or not states:
                        continue
                    elapsed = states[-1]["second"]
                    if elapsed <= seen.get(trial.name, -1):
                        continue
                    seen[trial.name] = elapsed
                    futures = {
                        side: pool.submit(observe, address)
                        for side, address in [("a", "192.168.56.11"), ("b", "192.168.56.12")]
                    }
                    frame = {
                        "reference_state_second": elapsed,
                        "reference_state_written_at": file.stat().st_mtime,
                        "edges": {side: future.result() for side, future in futures.items()},
                    }
                    observations.setdefault(trial.name, []).append(frame)
                    target = trial / "edge-observations.json"
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(
                            {
                                "evidence_level": "real_edge_observations",
                                "read_only": True,
                                "sampling": "after each new five-second API state; query latency recorded",
                                "frames": observations[trial.name],
                            },
                            indent=2,
                        )
                    )
                    temporary.replace(target)
                time.sleep(1)
    finally:
        if runner.poll() is None:
            os.killpg(runner.pid, signal.SIGINT)
            runner.wait(timeout=40)
    if runner.returncode != 0:
        subprocess.run(
            [sys.executable, "-m", "lab.report_live", "--output", str(destination)],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        raise RuntimeError(
            "Repeated experiment stopped; inspect preserved trial/journal/log artifacts"
        )
subprocess.run(
    [sys.executable, "-m", "lab.report_live", "--output", str(destination)], cwd=root, check=True
)
result = json.loads((destination / "results.json").read_text())
print(
    json.dumps(
        {
            "complete": result["complete"],
            "trials": len(result["trials"]),
            "valid": sum(row.get("valid", False) for row in result["trials"]),
            "passed": sum(row.get("passed", False) for row in result["trials"]),
        }
    ),
    flush=True,
)

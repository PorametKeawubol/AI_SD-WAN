"""Start/stop the live HTTPS decision API using TimescaleDB and private credentials."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "lab/runtime/live-dashboard.pid"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--stream", default="live", help="TimescaleDB telemetry and state stream")
    parser.add_argument(
        "--experimental-synthetic-forecast",
        action="store_true",
        help="Enable the frozen synthetic model on live probes for lab experiments",
    )
    args = parser.parse_args()
    if os.name != "posix" or not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use the prepared isolated Linux lab")
    if args.stop:
        pid = int(PID_FILE.read_text())
        process = Path(f"/proc/{pid}/cmdline")
        if not process.exists():
            print("Live dashboard is already stopped.")
            return
        if str(Path(__file__).resolve()).encode() not in process.read_bytes().split(b"\0"):
            raise RuntimeError("Saved PID belongs to another process; it was preserved")
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            if not process.exists() or b"Z" in Path(f"/proc/{pid}/stat").read_bytes().split()[2:3]:
                print("Live API stopped; lab VMs/controller remain running.")
                return
            time.sleep(0.5)
        raise RuntimeError("Dashboard is still shutting down; no force kill was sent")
    for port in (8000, 8001):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is in use; the existing service was preserved")
    os.chdir(ROOT)
    if args.background:
        log_path = ROOT / "lab/runtime/logs/live-dashboard.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), "--stream", args.stream]
        if args.experimental_synthetic_forecast:
            command.append("--experimental-synthetic-forecast")
        with log_path.open("ab") as log:
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        PID_FILE.write_text(str(child.pid))
        print(
            f"Live API starting: PID {child.pid}; https://localhost:8000; Grafana: https://localhost:3000"
        )
        return
    credentials = json.loads((ROOT / "lab/runtime/credentials.json").read_text())
    os.environ["FLEXIWAN_ACCESS_KEY"] = credentials["access_key"]
    private = json.loads((ROOT / "lab/runtime/observability/private.json").read_text())
    os.environ["SDWAN_DATABASE_URL"] = private["database_url"]
    os.environ["SDWAN_API_TOKEN"] = private["api_token"]
    os.environ["SDWAN_TLS_CERT"] = str(ROOT / "lab/runtime/observability/certs/api/server.crt")
    os.environ["SDWAN_TLS_KEY"] = str(ROOT / "lab/runtime/observability/certs/api/server.key")
    os.environ["SDWAN_CA_FILE"] = str(ROOT / "lab/runtime/certs/certificate.pem")
    from sdwan.cli import main as serve

    sys.argv = [
        "sdwan",
        "serve",
        "--flexiwan-config",
        str(ROOT / "config/flexiwan.local.json"),
        "--stream",
        args.stream,
    ]
    if args.experimental_synthetic_forecast:
        sys.argv.append("--experimental-synthetic-forecast")
    serve()


if __name__ == "__main__":
    main()

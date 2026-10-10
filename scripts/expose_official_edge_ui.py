"""Expose the installed vendor flexiEdge WebUI through owned loopback SSH relays."""

import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path("/opt/ai-sdwan-lab")
FORWARDS = (
    "127.0.0.1:18081:127.0.0.1:8080",
    "127.0.0.1:18082:192.168.56.12:8080",
)
TARGET = "lab@192.168.56.11"
STATE = ROOT / "lab/runtime/official-gui-relay.json"


def owns_relay(pid: int) -> bool:
    try:
        process = Path("/proc") / str(pid)
        args = (process / "cmdline").read_bytes().split(b"\0")
        return (
            Path(os.fsdecode(args[0])).name == "ssh"
            and all(value.encode() in args for value in FORWARDS)
            and TARGET.encode() in args
            and process.stat().st_uid == 0
        )
    except (OSError, IndexError):
        return False


def main() -> None:
    if os.geteuid() != 0 or not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Run as root inside the owned AI-SDWAN-Lab distribution")
    if os.environ.get("WSL_DISTRO_NAME") != "AI-SDWAN-Lab":
        raise RuntimeError("The official GUI relay belongs to AI-SDWAN-Lab")
    if not (ROOT / "lab/runtime/official-gui-login.json").is_file():
        raise RuntimeError("The dedicated flexiEdge GUI accounts have not been provisioned")

    pid = None
    if STATE.is_file():
        saved = json.loads(STATE.read_text())
        candidate = saved.get("pid")
        if isinstance(candidate, int) and owns_relay(candidate):
            pid = candidate

    if pid is None:
        for port in (18081, 18082):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
        args = ["ssh", "-f", "-N"]
        for forward in FORWARDS:
            args.extend(["-L", forward])
        args.extend(
            [
                "-i",
                str(ROOT / "lab/runtime/edge-key"),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=" + str(ROOT / "lab/runtime/known_hosts"),
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=20",
                "-o",
                "ServerAliveCountMax=3",
                TARGET,
            ]
        )
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=20)
        matches = [
            int(p.name)
            for p in Path("/proc").iterdir()
            if p.name.isdigit() and owns_relay(int(p.name))
        ]
        if len(matches) != 1:
            raise RuntimeError("Could not identify the owned GUI relay")
        pid = matches[0]
        STATE.write_text(
            json.dumps(
                {
                    "purpose": "Official packaged flexiEdge WebUI loopback access",
                    "pid": pid,
                    "ports": {"18081": "sdw-edge-a:8080", "18082": "sdw-edge-b:8080"},
                },
                indent=2,
            )
            + "\n"
        )

    for port in (18081, 18082):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            body = response.read().decode("utf-8")
            if response.status != 200 or "FlexiEdge" not in body:
                raise RuntimeError(f"The vendor GUI did not verify on port {port}")
        print(f"Official flexiEdge GUI: http://localhost:{port}/")
    print(f"Owned SSH relay PID: {pid}")


if __name__ == "__main__":
    main()

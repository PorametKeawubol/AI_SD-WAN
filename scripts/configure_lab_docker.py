"""Give only the owned WSL lab Docker Engine a dedicated Unix socket."""

import argparse
import json
import os
import socket
import subprocess
from pathlib import Path

ROOT = Path("/opt/ai-sdwan-lab")
ENDPOINT = "unix:///run/ai-sdwan-docker.sock"
DROPIN = Path("/etc/systemd/system/docker.socket.d/90-ai-sdwan-lab.conf")
CONTENT = "[Socket]\nListenStream=\nListenStream=/run/ai-sdwan-docker.sock\n"


def verify():
    result = subprocess.run(
        ["/usr/bin/docker", "--host", ENDPOINT, "info", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    info = json.loads(result.stdout)
    if (
        info["Name"] == "docker-desktop"
        or info["ServerVersion"] != "29.1.3"
        or info["DockerRootDir"] != "/var/lib/docker"
    ):
        raise RuntimeError("Dedicated endpoint does not identify the pinned native lab Engine")
    return {key: info[key] for key in ("Name", "ServerVersion", "DockerRootDir")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or os.environ.get("WSL_DISTRO_NAME") != "AI-SDWAN-Lab"
        or not (ROOT / ".ai-sdwan-owned").is_file()
    ):
        raise RuntimeError("Use root in the owned AI-SDWAN-Lab distribution")
    if DROPIN.is_symlink() or DROPIN.parent.resolve() != DROPIN.parent:
        raise RuntimeError("Socket override has an unexpected symlink")
    if DROPIN.exists() and DROPIN.read_text() != CONTENT:
        raise RuntimeError("Unknown socket override preserved for inspection")
    if DROPIN.exists() and Path("/run/ai-sdwan-docker.sock").exists():
        print(json.dumps({"changed": False, "endpoint": ENDPOINT, **verify()}))
        return
    if not args.apply:
        raise RuntimeError("Dedicated lab socket is not configured; use --apply to install it")
    for port in (8000, 8001):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Stop lab policy services before changing its Engine socket")
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(CONTENT)
    for arguments in (
        ["stop", "docker.service", "docker.socket"],
        ["daemon-reload"],
        ["start", "docker.socket", "docker.service"],
    ):
        subprocess.run(["systemctl", *arguments], check=True, timeout=30)
    print(json.dumps({"changed": True, "endpoint": ENDPOINT, **verify()}))


if __name__ == "__main__":
    main()

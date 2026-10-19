"""Own one scoped workload server and stop it when its runner disappears.

The runner starts this module inside an exact owned network namespace. No
arbitrary command, namespace, bind address, or PID to terminate is accepted.
"""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from lab.iperf_workload import background_server_command

ROOT = Path(__file__).resolve().parents[1]
PROC_ROOT = Path("/proc")
NETNS_ROOT = Path("/run/netns")


def scoped_command(kind, link=None, side=None):
    """Return the exact namespace and foreground server argv."""
    if kind == "receiver":
        command = background_server_command(link, side)
        return f"sdw-load{link[-1]}{side}", command
    if kind not in ("http", "udp") or link is not None or side is not None:
        raise ValueError("Choose one scoped receiver, HTTP or UDP server")
    module, port = (
        ("lab.application_traffic", "8088") if kind == "http" else ("lab.traffic", "5209")
    )
    return "sdw-server", [
        sys.executable,
        "-m",
        module,
        "server",
        "--host",
        "10.30.0.2",
        "--port",
        port,
    ]


def require_scope(namespace):
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Workload guard requires root on the isolated Linux lab")
    if not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use the prepared owned Linux lab")
    current = (PROC_ROOT / "self/ns/net").stat()
    expected = (NETNS_ROOT / namespace).stat()
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError("Workload guard is outside its exact owned namespace")


def parent_identity(parent):
    """Verify command tokens and retain kernel start ticks to detect PID reuse."""
    if type(parent) is not int or parent <= 1:
        raise ValueError("Invalid workload runner PID")
    directory = PROC_ROOT / str(parent)
    arguments = (directory / "cmdline").read_bytes().split(b"\0")
    if b"lab.real_traffic" not in arguments or b"start" not in arguments:
        raise RuntimeError("Parent PID does not identify the real traffic runner")
    # comm may contain spaces or parentheses; fields after its final ')' start
    # at process state (field 3), making starttime (field 22) index 19.
    tail = (directory / "stat").read_text().rpartition(") ")[2].split()
    if len(tail) < 20 or not tail[19].isdigit():
        raise RuntimeError("Parent process identity is unavailable")
    return int(tail[19])


def parent_is_active(parent, identity, now=None):
    """A stopped, stale or replaced parent is never permitted to keep a server."""
    try:
        if parent_identity(parent) != identity:
            return False
        document = json.loads((ROOT / "lab/runtime/real-traffic.json").read_text())
        if not isinstance(document, dict) or document.get("kind") != "real_traffic":
            return False
        if type(document.get("pid")) is not int or document["pid"] != parent:
            return False
        starting = document.get("starting") is True
        if not starting and document.get("active") is not True:
            return False
        updated = document.get("updated_at")
        if (
            isinstance(updated, bool)
            or not isinstance(updated, (int, float))
            or not math.isfinite(updated)
        ):
            return False
        age = (time.time() if now is None else now) - updated
        return 0 <= age <= (60 if starting else 25)
    except (OSError, ValueError, RuntimeError, UnicodeError):
        return False


def stop_child(child):
    """Signal only the Popen child this wrapper created, then reap it."""
    if child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if child.poll() is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
        child.wait(timeout=5)


def run(parent, kind, link=None, side=None, *, stopped=None):
    namespace, command = scoped_command(kind, link, side)
    require_scope(namespace)
    identity = parent_identity(parent)
    if not parent_is_active(parent, identity):
        raise RuntimeError("Workload runner heartbeat is unavailable or stale")
    stopped = threading.Event() if stopped is None else stopped
    child = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
    try:
        while not stopped.wait(0.5):
            if child.poll() is not None or not parent_is_active(parent, identity):
                return 1
        return 0
    finally:
        stop_child(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--kind", choices=("receiver", "http", "udp"), required=True)
    parser.add_argument("--link", choices=("wan1", "wan2", "wan3"))
    parser.add_argument("--side", choices=("a", "b"))
    args = parser.parse_args()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        return run(args.parent, args.kind, args.link, args.side, stopped=stopped)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Workload guard stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Own only sdw-* lab namespaces. Never change host/management qdiscs."""

import argparse
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from sdwan.scenarios import scenario as resolve_scenario

CAPACITY = {"wan1": 100, "wan2": 80, "wan3": 30}
BASE_DELAY = {"wan1": 12, "wan2": 20, "wan3": 32.5}
BASE_JITTER = {"wan1": 2.8, "wan2": 5, "wan3": 7}
SHAPING_LOCK = Path(__file__).resolve().parents[1] / "lab/runtime/wan-shaping.lock"
DOWN = {
    "S01": ["wan1"],
    "S02": ["wan2"],
    "S03": ["wan3"],
    "S04": ["wan1", "wan2"],
    "S05": ["wan1", "wan3"],
    "S06": ["wan2", "wan3"],
    "S07": list(CAPACITY),
}


def plan(scenario, elapsed=0):
    case = resolve_scenario(scenario)
    result = {
        link: {
            "delay_ms": BASE_DELAY[link],
            "jitter_ms": BASE_JITTER[link],
            "loss_pct": 0,
            "rate_mbps": capacity,
        }
        for link, capacity in CAPACITY.items()
    }
    for link in case.affected_links:
        if case.kind == "down":
            result[link]["loss_pct"] = 100
        elif case.kind in {"latency", "corrupt"}:
            result[link]["delay_ms"] = 240
        elif case.kind == "jitter":
            result[link].update(delay_ms=240, jitter_ms=150)
        elif case.kind == "loss":
            result[link]["loss_pct"] = 8
        elif case.kind == "ramp":
            factor = min(1, max(0, elapsed / 60))
            result[link].update(
                delay_ms=BASE_DELAY[link] + 210 * factor,
                jitter_ms=BASE_JITTER[link] + 70 * factor,
                loss_pct=5 * factor,
            )
        elif case.kind == "flap" and int(elapsed / 15) % 2 == 0:
            result[link]["loss_pct"] = 100
    if case.kind == "mixed":
        latency_link, loss_link = case.affected_links
        result[latency_link]["delay_ms"] = 240
        result[loss_link]["loss_pct"] = 9
    load_link = case.links[0] if case.kind == "saturation" else None
    return {
        "scenario": scenario,
        "wans": result,
        "background_load_mbps": CAPACITY[load_link] * 0.98 if load_link else 0,
        "background_load_link": load_link,
        "telemetry_fault": case.kind if case.group == "telemetry" else None,
        "telemetry_links": list(case.affected_links) if case.group == "telemetry" else [],
        "measurement": "real_netem_and_packets",
    }


def run(*arguments):
    subprocess.run(list(arguments), check=True)


def require_linux():
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Network changes require root on the isolated Linux lab host")


def setup():
    require_linux()
    existing = subprocess.check_output(["ip", "netns", "list"], text=True)
    if any(f"sdw-{name}" in existing for name in ("wan1", "wan2", "wan3", "client", "server")):
        raise RuntimeError("Lab namespaces already exist; no existing namespace was overwritten")
    # Check all bridges before creating any link.
    for bridge in [f"sdw-w{i}{s}" for i in range(1, 4) for s in ("a", "b")] + [
        "sdw-lan-a",
        "sdw-lan-b",
    ]:
        run("ip", "link", "show", bridge)
    for index in range(1, 4):
        namespace = f"sdw-wan{index}"
        run("ip", "netns", "add", namespace)
        run("ip", "-n", namespace, "link", "add", "br0", "type", "bridge")
        run("ip", "-n", namespace, "addr", "add", f"172.16.{index}.254/24", "dev", "br0")
        run("ip", "-n", namespace, "link", "set", "br0", "up")
        run("ip", "-n", namespace, "link", "set", "lo", "up")
        for side in ("a", "b"):
            outside = f"sdw-v{index}{side}"
            run(
                "ip",
                "link",
                "add",
                outside,
                "type",
                "veth",
                "peer",
                "name",
                side,
                "netns",
                namespace,
            )
            run("ip", "link", "set", outside, "master", f"sdw-w{index}{side}")
            run("ip", "link", "set", outside, "up")
            run("ip", "-n", namespace, "link", "set", "dev", side, "master", "br0")
            run("ip", "-n", namespace, "link", "set", "dev", side, "up")
    for name, side, subnet in (("client", "a", 20), ("server", "b", 30)):
        namespace = "sdw-" + name
        outside = "sdw-" + name + "-v"
        run("ip", "netns", "add", namespace)
        run(
            "ip", "link", "add", outside, "type", "veth", "peer", "name", "eth0", "netns", namespace
        )
        run("ip", "link", "set", outside, "master", "sdw-lan-" + side)
        run("ip", "link", "set", outside, "up")
        run("ip", "-n", namespace, "addr", "add", f"10.{subnet}.0.2/24", "dev", "eth0")
        run("ip", "-n", namespace, "link", "set", "eth0", "up")
        run("ip", "-n", namespace, "link", "set", "lo", "up")
        run("ip", "-n", namespace, "route", "add", "default", "via", f"10.{subnet}.0.1")
    apply(plan("CONTROL"))


@contextmanager
def shaping_session():
    """Keep one owner of WAN qdiscs; the kernel releases this lease on exit."""
    require_linux()
    import fcntl

    SHAPING_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with SHAPING_LOCK.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("WAN shaping is in use; stop the natural WAN mode first") from None
        active = True

        def update(configuration, *, change=False):
            if not active:
                raise RuntimeError("WAN shaping lease has expired")
            _apply(configuration, change=change)

        try:
            yield update
        finally:
            active = False
            fcntl.flock(handle, fcntl.LOCK_UN)


def assert_shaping_available():
    with shaping_session():
        pass


def apply(configuration):
    with shaping_session() as update:
        update(configuration)


def _apply(configuration, *, change=False):
    for link, values in configuration["wans"].items():
        if link not in CAPACITY:
            raise ValueError("Unknown WAN namespace")
        for side in ("a", "b"):
            arguments = [
                "ip",
                "netns",
                "exec",
                "sdw-" + link,
                "tc",
                "qdisc",
                "change" if change else "replace",
                "dev",
                side,
                "root",
                "netem",
                "delay",
                f"{values['delay_ms']}ms",
                f"{values['jitter_ms']}ms",
                f"{values.get('delay_correlation_pct', 0)}%",
                "distribution",
                "normal",
            ]
            if "burst_loss" in values:
                arguments += ["loss", "gemodel"] + [
                    f"{values['burst_loss'][key]}%"
                    for key in ("enter_bad_pct", "exit_bad_pct", "bad_loss_pct", "good_loss_pct")
                ]
            else:
                arguments += ["loss", f"{values['loss_pct']}%"]
            arguments += [
                "rate",
                f"{values['rate_mbps']}mbit",
            ]
            run(*arguments)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "plan", "apply", "reset"])
    parser.add_argument("--scenario", default="CONTROL")
    parser.add_argument("--elapsed", type=float, default=0)
    args = parser.parse_args()
    configuration = plan("CONTROL" if args.action == "reset" else args.scenario, args.elapsed)
    if args.action == "setup":
        setup()
    elif args.action in {"apply", "reset"}:
        apply(configuration)
    else:
        print(json.dumps(configuration, indent=2))

"""Explicit, reversible OSPF timer tuning for the two isolated generated edges."""

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path

BACKUP = Path("/var/lib/ai-sdwan/ospf-timers.json")
TUNED = {"hello_seconds": 1, "dead_seconds": 4}
DEFAULT = {"hello_seconds": 10, "dead_seconds": 40}


def cli(arguments):
    return subprocess.run(
        arguments, capture_output=True, text=True, check=True, timeout=5, restore_signals=False
    ).stdout


def interfaces_for_site(document, addresses, site, hostname):
    if site not in (1, 2) or hostname != f"sdw-edge-{'a' if site == 1 else 'b'}":
        raise ValueError("Unexpected lab edge identity")
    management = [row for row in addresses if row.get("ifname") == "eth0"]
    if (
        len(management) != 1
        or management[0].get("address") != f"52:54:00:53:0{site}:00"
        or not any(
            item.get("local") == f"192.168.56.{10 + site}"
            for item in management[0].get("addr_info", [])
        )
    ):
        raise ValueError("Management NIC differs from the generated lab VM")
    expected = {f"10.100.0.{2 * index + site + 1}": f"wan{index}" for index in (1, 2, 3)}
    result = {}
    for name, value in document["interfaces"].items():
        address = value.get("ipAddress")
        if address not in expected:
            continue
        if (
            not name.startswith("vpp")
            or not name[3:].isdigit()
            or not value.get("ifUp")
            or not value.get("ospfEnabled")
            or value.get("ipAddressPrefixlen") != 31
            or value.get("networkType") != "POINTOPOINT"
            or value.get("cost") != 10
            or value.get("area") != "0.0.0.0"
            or value.get("routerId") != f"192.168.56.{10 + site}"
            or expected[address] in result
        ):
            raise ValueError("Tunnel identity or OSPF topology differs from the lab")
        result[expected[address]] = {
            "interface": name,
            "address": address,
            "hello_seconds": value["timerMsecs"] / 1000,
            "dead_seconds": value["timerDeadSecs"],
            "adjacent_neighbors": value.get("nbrAdjacentCount", 0),
        }
    if set(result) != {"wan1", "wan2", "wan3"}:
        raise ValueError("Exactly three generated OSPF tunnel interfaces required")
    return result


def inspect(site):
    return interfaces_for_site(
        json.loads(cli(["vtysh", "-c", "show ip ospf interface json"])),
        json.loads(cli(["ip", "-json", "address", "show"])),
        site,
        socket.gethostname(),
    )


def timers(value):
    return {key: value[key] for key in TUNED}


def configure(site, *, apply=False, restore=False, backup=BACKUP):
    before = inspect(site)
    if not apply and not restore:
        return {"site": site, "changed": False, "interfaces": before}
    if any(timers(value) not in (DEFAULT, TUNED) for value in before.values()):
        raise ValueError("Unknown existing timers were preserved for inspection")
    if backup.exists():
        saved = json.loads(backup.read_text(encoding="utf-8"))
        if saved.get("site") != site or any(
            saved["interfaces"].get(link, {}).get("address") != value["address"]
            or saved["interfaces"].get(link, {}).get("interface") != value["interface"]
            or timers(saved["interfaces"][link]) != DEFAULT
            for link, value in before.items()
        ):
            raise ValueError("Saved timer backup differs from this lab topology")
    elif restore:
        raise ValueError("No original OSPF timer backup; restore refused")
    else:
        if any(timers(value) != DEFAULT for value in before.values()):
            raise ValueError("Original default timers were not established before tuning")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("x", encoding="utf-8") as stream:
            json.dump({"site": site, "created_at": time.time(), "interfaces": before}, stream)
    desired = DEFAULT if restore else TUNED
    changed = any(timers(value) != desired for value in before.values())
    if changed:
        command = ["vtysh", "-c", "configure terminal"]
        for value in before.values():
            command += [
                "-c",
                "interface " + value["interface"],
                "-c",
                f"ip ospf hello-interval {desired['hello_seconds']}",
                "-c",
                f"ip ospf dead-interval {desired['dead_seconds']}",
                "-c",
                "exit",
            ]
        command += ["-c", "end", "-c", "write memory"]
        cli(command)
    after = inspect(site)
    if any(timers(value) != desired for value in after.values()):
        raise RuntimeError("OSPF timer readback did not match; original backup retained")
    return {"site": site, "changed": changed, "before": before, "interfaces": after}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=int, choices=(1, 2), required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(args.site, apply=args.apply, restore=args.restore)))

"""Resume existing lab networks/VMs after a clean host reboot; preserve all data."""

import argparse
import json
import os
import re
import socket
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from lab.impairments import require_linux, setup
from lab.linux_topology import network_matches, network_xml

ROOT = Path(__file__).resolve().parents[1]


def output(*arguments):
    return subprocess.check_output(
        arguments, text=True, env={**os.environ, "LC_ALL": "C"}, timeout=30
    )


def vm_matches(text, side, root=ROOT):
    node = ET.fromstring(text)
    site = 1 if side == "a" else 2
    disks = {
        row.find("source").get("file")
        for row in node.findall("devices/disk")
        if row.find("source") is not None
    }
    expected_disks = {
        str(root / f"lab/runtime/edge-{side}" / name) for name in ("disk.qcow2", "seed.iso")
    }
    interfaces = node.findall("devices/interface")
    expected_networks = ["sdw-mgmt", *[f"sdw-wan{i}{side}" for i in range(1, 4)], f"sdw-lan-{side}"]
    observed = {
        (
            row.find("source").get("network"),
            row.find("mac").get("address"),
            row.find("model").get("type"),
        )
        for row in interfaces
        if all(row.find(tag) is not None for tag in ("source", "mac", "model"))
    }
    return (
        node.findtext("name") == f"sdw-edge-{side}"
        and node.get("type") in {"kvm", "qemu"}
        and disks == expected_disks
        and len(interfaces) == 5
        and observed
        == {
            (network, f"52:54:00:53:0{site}:0{nic}", "virtio")
            for nic, network in enumerate(expected_networks)
        }
    )


def verified_namespaces():
    expected = {"sdw-wan1", "sdw-wan2", "sdw-wan3", "sdw-client", "sdw-server"}
    existing = {row.split()[0] for row in output("ip", "netns", "list").splitlines()}
    present = existing & expected
    if not present:
        return False
    if present != expected:
        raise RuntimeError("Partial lab namespaces were preserved; inspect before resuming")
    for namespace in sorted(expected):
        wan = namespace.startswith("sdw-wan")
        index = int(namespace[-1]) if wan else None
        inside = json.loads(output("ip", "-n", namespace, "-json", "-details", "link", "show"))
        by_name = {row["ifname"]: row for row in inside}
        for side in ("a", "b") if wan else ("eth0",):
            outside = f"sdw-v{index}{side}" if wan else namespace + "-v"
            bridge = (
                f"sdw-w{index}{side}"
                if wan
                else "sdw-lan-" + ("a" if namespace == "sdw-client" else "b")
            )
            host = json.loads(output("ip", "-json", "-details", "link", "show", "dev", outside))[0]
            peer = by_name.get(side, {})
            if (
                host.get("linkinfo", {}).get("info_kind") != "veth"
                or peer.get("linkinfo", {}).get("info_kind") != "veth"
                or host.get("master") != bridge
                or host.get("link_index") != peer.get("ifindex")
                or peer.get("link_index") != host.get("ifindex")
                or (wan and peer.get("master") != "br0")
            ):
                raise RuntimeError("Existing namespace link differs from the exact lab topology")
        interface = "br0" if wan else "eth0"
        address = (
            f"172.16.{index}.254"
            if wan
            else ("10.20.0.2" if namespace == "sdw-client" else "10.30.0.2")
        )
        rows = json.loads(
            output("ip", "-n", namespace, "-json", "address", "show", "dev", interface)
        )
        ipv4 = {
            (entry["local"], entry["prefixlen"])
            for row in rows
            for entry in row["addr_info"]
            if entry["family"] == "inet"
        }
        if ipv4 != {(address, 24)}:
            raise RuntimeError("Existing namespace subnet differs; addresses were preserved")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_linux()
    if not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Expected the isolated lab ownership marker")
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 8001)) == 0:
            raise RuntimeError("A live experiment is running; no resources were changed")
    networks = [
        ("sdw-mgmt", "sdw-mgmt", "192.168.56.1"),
        ("sdw-lan-a", "sdw-lan-a", None),
        ("sdw-lan-b", "sdw-lan-b", None),
    ]
    networks += [
        (f"sdw-wan{i}{side}", f"sdw-w{i}{side}", None) for i in range(1, 4) for side in ("a", "b")
    ]
    pending_networks, pending_vms = [], []
    for name, bridge, address in networks:
        if not network_matches(
            output("virsh", "net-dumpxml", name), network_xml(name, bridge, address)
        ):
            raise RuntimeError("Existing libvirt network differs; all definitions were preserved")
        if re.search(r"(?m)^Active:\s+no\s*$", output("virsh", "net-info", name)):
            if Path("/sys/class/net", bridge).exists():
                raise RuntimeError(
                    "Inactive network has a stale bridge; inspect before restarting it"
                )
            pending_networks.append(name)
    for side in ("a", "b"):
        name = "sdw-edge-" + side
        if not vm_matches(output("virsh", "dumpxml", name, "--inactive"), side):
            raise RuntimeError("Existing VM differs from the saved lab disks/NICs; preserved")
        state = output("virsh", "domstate", name).strip()
        if state == "shut off":
            pending_vms.append(name)
        elif state != "running":
            raise RuntimeError("Lab VM state requires inspection before resuming")
    namespaces_ready = verified_namespaces()
    print(
        json.dumps(
            {
                "networks_to_start": pending_networks,
                "vms_to_start": pending_vms,
                "namespaces_ready": namespaces_ready,
                "apply": args.apply,
            }
        )
    )
    if args.apply:
        for name in pending_networks:
            subprocess.run(["virsh", "net-start", name], check=True)
        if not namespaces_ready:
            setup()
        subprocess.run(
            [
                "/usr/bin/docker",
                "--host",
                "unix:///run/ai-sdwan-docker.sock",
                "compose",
                "-f",
                "lab/compose.yaml",
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "120",
            ],
            cwd=ROOT,
            env={**os.environ, "LAB_BIND_IP": "192.168.56.1"},
            check=True,
        )
        for name in pending_vms:
            subprocess.run(["virsh", "start", name], check=True)
        if (ROOT / "lab/runtime/observability/private.json").is_file():
            subprocess.run(
                [
                    "/usr/bin/docker",
                    "--host",
                    "unix:///run/ai-sdwan-docker.sock",
                    "compose",
                    "-f",
                    "lab/observability.compose.yaml",
                    "up",
                    "-d",
                    "--no-build",
                    "--wait",
                    "--wait-timeout",
                    "120",
                ],
                cwd=ROOT,
                check=True,
            )
        print("Existing lab resumed; allow the edges to reconnect, then run preflight.")


if __name__ == "__main__":
    main()

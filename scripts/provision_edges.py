"""Provision only the two generated lab VMs using the pinned controller API.

Requires actual enrolled flexiEdge VMs; cannot run against protocol fixtures.
Checks jobs, router state, three tunnels, installed agent policy and real probes.
"""

import argparse
import copy
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sdwan.collector import FlexiWANTelemetryCollector
from sdwan.flexiwan import FlexiManageClient, FlexiWANConfig, IntegrationError, label_id

ROOT = Path(__file__).resolve().parents[1]


def scoped_tunnels(tunnels, devices, path_labels, labels):
    """Match existing tunnels by both VM/interface pairs and the dedicated WAN label."""
    if set(path_labels) != {"wan1", "wan2", "wan3"}:
        raise IntegrationError("Exactly three named lab WAN bindings required")
    names = {labels[identifier]["name"]: link for link, identifier in path_labels.items()}
    if len(names) != 3:
        raise IntegrationError("Lab WAN label names must be distinct")
    expected = {}
    for link in ("wan1", "wan2", "wan3"):
        index = int(link[-1])
        expected[link] = set()
        for site, device in enumerate(devices, 1):
            mac = f"52:54:00:53:0{site}:0{index}"
            interfaces = [row for row in device["interfaces"] if row["MAC"].lower() == mac]
            if len(interfaces) != 1:
                raise IntegrationError("Cannot identify the exact lab WAN interface")
            expected[link].add((device["_id"], interfaces[0]["_id"]))
    matched = {}
    for tunnel in tunnels:
        link = names.get((tunnel.get("pathlabel") or {}).get("name"))
        pair = {
            ((tunnel.get("deviceA") or {}).get("_id"), tunnel.get("interfaceA")),
            ((tunnel.get("deviceB") or {}).get("_id"), tunnel.get("interfaceB")),
        }
        if link not in expected or pair != expected[link] or link in matched:
            raise IntegrationError("Existing tunnel differs from the exact lab WAN scope")
        matched[link] = tunnel
    return matched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", default="lab/runtime/credentials.json")
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    os.environ["FLEXIWAN_ACCESS_KEY"] = credentials["access_key"]
    configuration = FlexiWANConfig.model_validate(
        {
            "base_url": credentials.get("url", "https://192.168.56.1:3443") + "/api",
            "organization_id": credentials["organization_id"],
            "device_ids": ["000000000000000000000001"],
            "policy_id": credentials["policy_id"],
            "rule_id": credentials["rule_id"],
            "ca_file": credentials["ca_file"],
            "links": {
                link: {
                    "path_label_id": credentials["path_labels"][link],
                    "capacity_mbps": capacity,
                }
                for link, capacity in (("wan1", 100), ("wan2", 80), ("wan3", 30))
            },
        }
    )
    client = FlexiManageClient(configuration)
    client.verify_version()
    devices = client.devices()
    by_host = {device["hostname"]: device for device in devices}
    if len(devices) != 2 or set(by_host) != {"sdw-edge-a", "sdw-edge-b"}:
        raise IntegrationError(
            "Expected exactly two enrolled lab VMs; other devices were not modified"
        )
    devices = [by_host["sdw-edge-a"], by_host["sdw-edge-b"]]
    for device in devices:
        if device.get("versions", {}).get("agent") != "6.3.35":
            raise IntegrationError(
                "Edge package differs from pinned 6.3.35; verify compatibility before provisioning"
            )
    configuration.device_ids = [device["_id"] for device in devices]
    configuration.telemetry_device_id = configuration.device_ids[0]
    if not args.apply:
        print(
            json.dumps(
                {
                    "scope": configuration.device_ids,
                    "edge_version": "6.3.35",
                    "actions": [
                        "approve and assign four data interfaces",
                        "start VPP",
                        "create three labeled tunnels",
                        "install dedicated policy",
                        "install VPP probe",
                        "verify jobs and agent readback",
                    ],
                },
                indent=2,
            )
        )
        return
    known_hosts = ROOT / "lab/runtime/known_hosts"
    if not known_hosts.is_file():
        raise IntegrationError("Connect to both lab edges using lab/runtime/known_hosts first")
    ssh_options = [
        "-i",
        args.ssh_key,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(known_hosts),
        "-o",
        "ConnectTimeout=10",
    ]
    labels = {row["_id"]: row for row in client.request("GET", "pathlabels")}
    for site, device in enumerate(devices, 1):
        interfaces = copy.deepcopy(device["interfaces"])
        for interface in interfaces:
            expected = f"52:54:00:53:0{site}:"
            if not interface["MAC"].lower().startswith(expected):
                raise IntegrationError("VM MAC addresses differ from generated topology")
            nic = int(interface["MAC"].split(":")[-1], 16)
            if nic == 0:
                interface["isAssigned"] = False
                continue
            interface.update(isAssigned=True, locked=False, dhcp="no", routing="OSPF")
            if nic in {1, 2, 3}:
                interface.update(
                    type="WAN",
                    routing="NONE",
                    IPv4=f"172.16.{nic}.{site}",
                    IPv4Mask="24",
                    gateway=f"172.16.{nic}.254",
                    metric=str(100 * nic),
                    useStun=False,
                    monitorInternet=False,
                    PublicIP=f"172.16.{nic}.{site}",
                    pathlabels=[labels[credentials["path_labels"][f"wan{nic}"]]],
                )
            elif nic == 4:
                interface.update(
                    type="LAN",
                    IPv4=f"10.{20 if site == 1 else 30}.0.1",
                    IPv4Mask="24",
                    gateway="",
                    metric="",
                    pathlabels=[],
                )
            else:
                raise IntegrationError("Unexpected VM interface")
        client.request(
            "PUT",
            f"devices/{device['_id']}",
            body={"name": device["hostname"], "isApproved": True, "interfaces": interfaces},
        )
        location = client.last_job_location
        if location:
            ids = [int(value) for value in parse_qs(urlsplit(location).query)["ids"][0].split(",")]
            client.wait_jobs({"status": "completed", "ids": ids}, [device])
        current = next(row for row in client.devices() if row["_id"] == device["_id"])
        if current.get("deviceStatus", {}).get("state") != "running":
            response = client.request(
                "POST",
                f"devices/{device['_id']}/apply",
                body={"method": "start", "meta": {"allowOverlapping": False}},
            )
            client.wait_jobs(response, [device])
    # A successful start job is followed by actual state polling, not assumed running.
    for _ in range(30):
        fresh = client.devices()
        if all(device.get("deviceStatus", {}).get("state") == "running" for device in fresh):
            break
        time.sleep(1)
    else:
        raise IntegrationError("VPP did not report running after start jobs")
    # Create one WAN at a time: concurrent tunnel transactions in the pinned
    # controller can partially complete while updating its shared tunnel counter.
    for link, identifier in credentials["path_labels"].items():
        existing = scoped_tunnels(
            client.request("GET", "tunnels"), devices, credentials["path_labels"], labels
        )
        if link in existing:
            if not all(existing[link].get(key) for key in ("deviceAconf", "deviceBconf")):
                raise IntegrationError(
                    "Existing tunnel has incomplete agent configuration; inspect its queue"
                )
            continue
        response = client.request(
            "POST",
            "devices/apply",
            body={
                "method": "tunnels",
                "devices": {device["_id"]: {} for device in devices},
                "meta": {
                    "topology": "fullMesh",
                    "pathLabels": [identifier],
                    "advancedOptions": {"routing": "ospf", "ospfCost": "10", "ospfArea": "0.0.0.0"},
                },
            },
        )
        client.wait_jobs(response, devices)
    tunnels = client.request("GET", "tunnels")
    if len(scoped_tunnels(tunnels, devices, credentials["path_labels"], labels)) != 3:
        raise IntegrationError("Expected three distinct WAN tunnels")
    for device in devices:
        current = next(row for row in client.devices() if row["_id"] == device["_id"])
        installed = current.get("policies", {}).get("multilink", {})
        if (
            label_id(installed.get("policy")) == configuration.policy_id
            and installed.get("status") == "installed"
        ):
            continue
        response = client.request(
            "POST",
            f"devices/{device['_id']}/apply",
            body={"method": "mlpolicy", "meta": {"op": "install", "id": configuration.policy_id}},
        )
        client.wait_jobs(response, [device])
    runtime = ROOT / "lab/runtime"
    for site, side in ((1, "a"), (2, "b")):
        target = f"lab@192.168.56.{10 + site}"
        ssh = ["ssh", *ssh_options, target]
        subprocess.run(ssh + ["sudo install -d /opt/ai-sdwan /etc/ai-sdwan"], check=True)
        # Linux PCI -> VPP interface mapping comes from the actual device, not guessed NIC order.
        mapping = client.request(
            "POST",
            f"devices/{devices[site - 1]['_id']}/send",
            body={
                "entity": "os",
                "api": "exec_timeout",
                "params": {"cmd": "vppctl show interface address", "timeout": 5},
            },
        )
        text = mapping.get("message", {}).get("output", "")
        bindings = {}
        current_interface = None
        for line in text.splitlines():
            if line and not line[0].isspace():
                current_interface = line.split()[0]
            for index in range(1, 4):
                if f"172.16.{index}.{site}/24" in line and current_interface:
                    bindings[f"wan{index}"] = {
                        "vpp_interface": current_interface,
                        "linux_interface": f"eth{index}",
                        "mac": f"52:54:00:53:0{site}:0{index}",
                        "target_ip": f"172.16.{index}.{3 - site}",
                        "capacity_mbps": (100, 80, 30)[index - 1],
                    }
        if len(bindings) != 3:
            raise IntegrationError("Cannot map all three actual VPP WAN interfaces")
        probe_config = runtime / f"probe-{side}.json"
        probe_config.write_text(
            json.dumps(
                {"links": bindings, "probe_method": "linux_tap", "lab_faults_enabled": True}
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "scp",
                *ssh_options,
                str(ROOT / "lab/edge/probe.py"),
                str(probe_config),
                target + ":/tmp/",
            ],
            check=True,
        )
        subprocess.run(
            ssh
            + [
                f"sudo install -m 0755 /tmp/probe.py /opt/ai-sdwan/probe.py && sudo install -m 0600 /tmp/probe-{side}.json /etc/ai-sdwan/probe.json"
            ],
            check=True,
        )
    policy, _ = client.preflight()
    client.readback(policy)
    samples = FlexiWANTelemetryCollector(client).collect()
    if len(samples) != 3 or not all(row["up"] for row in samples):
        raise IntegrationError(
            "Three actual VPP probes must be reachable before enabling the live service"
        )
    output = ROOT / "config/flexiwan.local.json"
    output.write_text(configuration.model_dump_json(indent=2), encoding="utf-8")
    (ROOT / "reports/flexiwan/edge-provisioning.json").write_text(
        json.dumps(
            {
                "timestamp": time.time(),
                "evidence_level": "real_edges",
                "edge_version": "6.3.35",
                "devices": configuration.device_ids,
                "tunnels": 3,
                "agent_readback": True,
                "actual_probes": samples,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Actual edges and probes verified; config/flexiwan.local.json created")


if __name__ == "__main__":
    main()

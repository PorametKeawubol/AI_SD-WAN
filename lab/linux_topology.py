"""Generate a real two-edge, three-WAN libvirt lab. Apply only on a Linux lab host.

No existing VM/network is destroyed. Management is separate from WAN impairments.
Cloud image input is supplied explicitly so its release and SHA256 can be recorded.
"""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "lab/runtime"


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def network_xml(name, bridge, address=None):
    ip = f'<ip address="{address}" netmask="255.255.255.0"/>' if address else ""
    forward = '<forward mode="nat"/>' if address else ""
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, "https://ai-sdwan.local/lab/" + name)
    return f'<network><name>{name}</name><uuid>{identifier}</uuid>{forward}<bridge name="{bridge}" stp="off" delay="0"/>{ip}</network>'


def generate_networks():
    definitions = [
        ("sdw-mgmt", "sdw-mgmt", "192.168.56.1"),
        ("sdw-lan-a", "sdw-lan-a", None),
        ("sdw-lan-b", "sdw-lan-b", None),
    ]
    definitions.extend(
        (f"sdw-wan{i}{side}", f"sdw-w{i}{side}", None) for i in range(1, 4) for side in ("a", "b")
    )
    for name, bridge, address in definitions:
        write_private(RUNTIME / "networks" / (name + ".xml"), network_xml(name, bridge, address))
    return [name for name, _, _ in definitions]


def network_matches(actual, expected):
    """Allow only this lab's deterministic UUID, bridge, forwarding and subnet."""
    actual, expected = ET.fromstring(actual), ET.fromstring(expected)
    for tag in ("name", "uuid", "bridge", "forward", "ip"):
        first, second = actual.find(tag), expected.find(tag)
        if (first is None) != (second is None):
            return False
        if first is not None and ((first.text or "").strip(), first.attrib) != (
            (second.text or "").strip(),
            second.attrib,
        ):
            return False
    return True


def apply_networks():
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Network creation requires root on the isolated Linux host")
    pending = []
    for path in sorted((RUNTIME / "networks").glob("sdw-*.xml")):
        existing = subprocess.run(
            ["virsh", "net-dumpxml", path.stem], capture_output=True, text=True, check=False
        )
        if existing.returncode == 0:
            if not network_matches(existing.stdout, path.read_text(encoding="utf-8")):
                raise RuntimeError(
                    "Existing network differs from this lab; nothing was overwritten"
                )
            info = subprocess.check_output(
                ["virsh", "net-info", path.stem], text=True, env={**os.environ, "LC_ALL": "C"}
            )
            if not re.search(r"(?m)^Active:\s+yes\s*$", info):
                raise RuntimeError("Existing lab network is inactive; inspect before starting VMs")
        else:
            pending.append(path)
    for path in pending:
        subprocess.run(["virsh", "net-define", str(path)], check=True)
        subprocess.run(["virsh", "net-start", path.stem], check=True)


def generate(ssh_public_key, controller_host, ca_file, token_file):
    ipaddress.IPv4Address(controller_host)
    key = Path(ssh_public_key).read_text(encoding="utf-8").strip()
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("OpenSSH public key required")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    ca = Path(ca_file).read_text(encoding="utf-8")
    names = generate_networks()
    for site, side in ((1, "a"), (2, "b")):
        ethernets = {}
        for nic in range(5):
            mac = f"52:54:00:53:0{site}:0{nic}"
            if nic == 0:
                addresses, routes = (
                    [f"192.168.56.{10 + site}/24"],
                    [{"to": "0.0.0.0/0", "via": "192.168.56.1", "metric": 10}],
                )
            elif nic <= 3:
                addresses, routes = (
                    [f"172.16.{nic}.{site}/24"],
                    [{"to": "0.0.0.0/0", "via": f"172.16.{nic}.254", "metric": 100 + 100 * nic}],
                )
            else:
                addresses, routes = [f"10.{20 if site == 1 else 30}.0.1/24"], []
            ethernets[f"nic{nic}"] = {
                "match": {"macaddress": mac},
                "set-name": f"eth{nic}",
                "addresses": addresses,
                "routes": routes,
                "nameservers": {"addresses": ["1.1.1.1"]},
            }
        agent = {
            "agent": {
                "server": f"https://{controller_host}:3443",
                "bypass_certificate": False,
                "token": "/etc/flexiwan/agent/token.txt",
                "monitor_wan": {
                    "monitor_unassigned_interfaces": False,
                    "servers": ["172.16.1.254", "172.16.2.254", "172.16.3.254"],
                },
            }
        }

        def embedded(source, destination):
            return {
                "path": destination,
                "permissions": "0755",
                "encoding": "b64",
                "content": base64.b64encode(source.read_bytes()).decode(),
            }

        user_data = {
            "hostname": f"sdw-edge-{side}",
            "manage_etc_hosts": True,
            "users": [
                {
                    "name": "lab",
                    "groups": "sudo",
                    "shell": "/bin/bash",
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "ssh_authorized_keys": [key],
                    "lock_passwd": True,
                }
            ],
            "ssh_pwauth": False,
            "package_update": True,
            "write_files": [
                embedded(ROOT / "lab/edge/install.sh", "/opt/ai-sdwan/install.sh"),
                embedded(ROOT / "lab/edge/probe.py", "/opt/ai-sdwan/probe.py"),
                embedded(ROOT / "lab/edge/crypto_fallback.py", "/opt/ai-sdwan/crypto_fallback.py"),
                {"path": "/usr/local/share/ca-certificates/ai-sdwan.crt", "content": ca},
                {"path": "/etc/flexiwan/agent/fwagent_conf.yaml", "content": yaml.safe_dump(agent)},
                {"path": "/etc/flexiwan/agent/token.txt", "permissions": "0600", "content": token},
            ],
            "runcmd": [
                ["update-ca-certificates"],
                ["bash", "/opt/ai-sdwan/install.sh"],
                ["python3", "/opt/ai-sdwan/crypto_fallback.py", "--apply"],
                ["fwsystem_checker", "--quiet"],
                ["fwagent", "start"],
            ],
        }
        folder = RUNTIME / f"edge-{side}"
        write_private(
            folder / "user-data", "#cloud-config\n" + yaml.safe_dump(user_data, sort_keys=False)
        )
        write_private(
            folder / "network-config", yaml.safe_dump({"version": 2, "ethernets": ethernets})
        )
        write_private(
            folder / "meta-data", f"instance-id: sdw-edge-{side}\nlocal-hostname: sdw-edge-{side}\n"
        )
    return names


def apply(image, expected_sha256):
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Apply requires root on an isolated Linux host")
    for executable in ("virsh", "virt-install", "qemu-img", "cloud-localds"):
        if not shutil.which(executable):
            raise RuntimeError(f"Install {executable} on the Linux lab host")
    image = Path(image).resolve()
    digest = hashlib.file_digest(image.open("rb"), "sha256").hexdigest()
    if digest != expected_sha256:
        raise ValueError("Ubuntu cloud image SHA256 differs from the supplied release checksum")
    for side in ("a", "b"):
        if (
            subprocess.run(
                ["virsh", "dominfo", f"sdw-edge-{side}"], capture_output=True, check=False
            ).returncode
            == 0
        ):
            raise RuntimeError("Lab VM already exists; no existing VM was replaced")
    apply_networks()
    hardware = Path("/dev/kvm").exists()
    for site, side in ((1, "a"), (2, "b")):
        folder = RUNTIME / f"edge-{side}"
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(image),
                str(folder / "disk.qcow2"),
                "20G",
            ],
            check=True,
        )
        subprocess.run(
            [
                "cloud-localds",
                "--network-config",
                str(folder / "network-config"),
                str(folder / "seed.iso"),
                str(folder / "user-data"),
                str(folder / "meta-data"),
            ],
            check=True,
        )
        arguments = [
            "virt-install",
            "--name",
            f"sdw-edge-{side}",
            "--import",
            "--memory",
            "4096",
            "--vcpus",
            "2",
            "--virt-type",
            "kvm" if hardware else "qemu",
            "--cpu",
            "host-passthrough" if hardware else "max",
            "--os-variant",
            "ubuntu20.04",
            "--disk",
            f"path={folder / 'disk.qcow2'},bus=virtio",
            "--disk",
            f"path={folder / 'seed.iso'},device=cdrom",
            "--graphics",
            "none",
            "--serial",
            f"file,path={folder / 'console.log'}",
            "--noautoconsole",
        ]
        networks = ["sdw-mgmt", *[f"sdw-wan{i}{side}" for i in range(1, 4)], f"sdw-lan-{side}"]
        for nic, network in enumerate(networks):
            arguments += [
                "--network",
                f"network={network},model=virtio,mac=52:54:00:53:0{site}:0{nic}",
            ]
        subprocess.run(arguments, check=True)
    (RUNTIME / "vm-image-evidence.json").write_text(
        json.dumps(
            {
                "image": str(image),
                "sha256": digest,
                "virtualization": "kvm" if hardware else "qemu-tcg",
                "edge_package": "6.3.35",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-public-key")
    parser.add_argument("--controller-host", default="192.168.56.1")
    parser.add_argument("--ca-file")
    parser.add_argument("--token-file")
    parser.add_argument("--networks-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--image-sha256")
    args = parser.parse_args()
    if args.networks_only:
        generate_networks()
        if args.apply:
            apply_networks()
        print("Lab network definitions prepared" + (" and applied" if args.apply else ""))
    else:
        if not all((args.ssh_public_key, args.ca_file, args.token_file)):
            parser.error("VM generation requires --ssh-public-key, --ca-file and --token-file")
        generate(args.ssh_public_key, args.controller_host, args.ca_file, args.token_file)
    if args.apply and not args.networks_only:
        if not args.image or not args.image_sha256:
            parser.error(
                "--apply requires --image and --image-sha256 from Ubuntu release SHA256SUMS"
            )
        apply(args.image, args.image_sha256)
    elif not args.networks_only:
        print("Generated lab/runtime; reviewed files are ready for Linux VM creation")

"""Record nonsecret version and KVM evidence for the forecast experiment."""

import argparse
import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree


def capture(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def collect():
    guests = []
    for name in ("sdw-edge-a", "sdw-edge-b"):
        result = capture(["virsh", "dumpxml", name])
        if result["exit_code"] == 0:
            root = ElementTree.fromstring(result["stdout"])
            guests.append(
                {
                    "name": name,
                    "vcpus": root.findtext("vcpu"),
                    "memory_kib": root.findtext("memory"),
                    "state": capture(["virsh", "domstate", name])["stdout"],
                }
            )
    cpuinfo = Path("/proc/cpuinfo").read_text()
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "linux": platform.platform(),
        "python": platform.python_version(),
        "packages": {
            package: importlib.metadata.version(package)
            for package in ("numpy", "xgboost", "scikit-learn", "fastapi", "matplotlib", "pytest")
        },
        "memory": [
            line
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        ],
        "cpu_virtualization_flags_present": any(flag in cpuinfo.split() for flag in ("svm", "vmx")),
        "kvm_module": capture(["/usr/sbin/modprobe", "kvm_amd"]),
        "kvm_kernel_evidence": capture(["/bin/sh", "-c", "dmesg | grep -i kvm | tail -n 5"]),
        "guests": guests,
        "controller_containers": capture(
            [
                "docker",
                "--host",
                "unix:///run/ai-sdwan-docker.sock",
                "ps",
                "--format",
                "{{.Names}} {{.Image}} {{.Status}}",
            ]
        ),
        "link_testbed": "six isolated veth namespaces, no flexiEdge in packet path",
        "restriction": "No VM benchmark results collected on this date; KVM unavailable. No OS rollback, reboot, security or boot setting changed.",
        "related_upstream_issue": "https://github.com/microsoft/WSL/issues/41917",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Existing environment evidence preserved")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(collect(), indent=2))
    print(str(args.output))


if __name__ == "__main__":
    main()

"""Verify or tune only the isolated lab's three tunnel timers on both edges."""

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-key", default="lab/runtime/edge-key")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", action="store_true")
    parser.add_argument("--output", default="reports/flexiwan/ospf-timers.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Existing evidence preserved; choose a fresh --output")
    if args.apply or args.restore:
        for port in (8000, 8001):
            with socket.socket() as check:
                if check.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Stop the dashboard/runner before changing OSPF timers")
    options = [
        "-i",
        str(Path(args.ssh_key).resolve()),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(ROOT / "lab/runtime/known_hosts"),
        "-o",
        "ConnectTimeout=5",
    ]
    code = (ROOT / "lab/edge/ospf.py").read_text(encoding="utf-8")
    changes = []
    for site in (1, 2):
        command = [
            "ssh",
            *options,
            f"lab@192.168.56.{10 + site}",
            "sudo python3 - --site",
            str(site),
        ]
        if args.apply:
            command += ["--apply"]
        if args.restore:
            command += ["--restore"]
        result = subprocess.run(
            command, input=code, capture_output=True, text=True, check=True, timeout=15
        )
        changes.append(json.loads(result.stdout))
    # Verification waits for actual adjacencies, rather than assuming a write converged.
    for attempt in range(20):
        current = []
        for site in (1, 2):
            result = subprocess.run(
                [
                    "ssh",
                    *options,
                    f"lab@192.168.56.{10 + site}",
                    "sudo python3 - --site",
                    str(site),
                ],
                input=code,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            current.append(json.loads(result.stdout))
        if all(
            row["adjacent_neighbors"] == 1
            for edge in current
            for row in edge["interfaces"].values()
        ):
            break
        if attempt == 19:
            raise RuntimeError("Three OSPF adjacencies were not re-established on both lab edges")
        time.sleep(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "observed_at": time.time(),
                "evidence_level": "real_edge_observations",
                "mode": "restore" if args.restore else "tune" if args.apply else "inspect",
                "changes": changes,
                "verified": current,
                "scope": "Three generated tunnel interfaces per edge; all strategies share these timers",
                "persistence": "FRR write memory; verify after edge/controller reconfiguration or reboot",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps({"output": str(output), "edges_verified": len(current), "interfaces": current})
    )


if __name__ == "__main__":
    main()

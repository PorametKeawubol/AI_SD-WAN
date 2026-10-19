"""Repair guest clock skew after Windows suspend; never change the Windows clock."""

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync_site(site, options, code, *, apply=False, force_sync=False):
    # SSH startup latency can leave the first clock-set request over a second old.
    # Retry only a measured clock-bound failure, with a fresh host epoch each time.
    attempts = 3 if apply else 1
    for attempt in range(1, attempts + 1):
        command = [
            "ssh",
            *options,
            f"lab@192.168.56.{10 + site}",
            "sudo python3 - --site",
            str(site),
            "--host-epoch",
            f"{time.time():.6f}",
        ]
        if apply:
            command += ["--apply"]
        if force_sync:
            command += ["--force-sync"]
        result = subprocess.run(
            command, input=code, capture_output=True, text=True, check=True, timeout=12
        )
        row = json.loads(result.stdout)
        row["after_offset_upper_bound_seconds"] = time.time() - row["guest_after_epoch"]
        row["synchronization_attempt"] = attempt
        if not apply or abs(row["after_offset_upper_bound_seconds"]) <= 1:
            return row
    raise RuntimeError(
        f"Guest clock readback still exceeds one second: site {site}, "
        f"{row['after_offset_upper_bound_seconds']:.3f}s after {attempts} attempts"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-sync", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.force_sync and not args.apply:
        parser.error("--force-sync requires --apply")
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Previous clock evidence preserved; use a fresh output")
    if not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use the owned isolated Linux lab")
    if args.apply:
        for port in (8000, 8001):
            with socket.socket() as check:
                if check.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Stop policy services before changing guest time")
    options = [
        "-i",
        str(ROOT / "lab/runtime/edge-key"),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(ROOT / "lab/runtime/known_hosts"),
        "-o",
        "ConnectTimeout=5",
    ]
    code = (ROOT / "lab/edge/clock.py").read_text(encoding="utf-8")
    rows = [
        sync_site(site, options, code, apply=args.apply, force_sync=args.force_sync)
        for site in (1, 2)
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "observed_at": time.time(),
                "evidence_level": "real_edge_clock_observations",
                "applied": args.apply,
                "source": "Owned WSL host realtime clock",
                "edges": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "edges": rows}))


if __name__ == "__main__":
    main()

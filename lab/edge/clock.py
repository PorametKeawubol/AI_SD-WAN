"""Inspect/synchronize the generated edge's realtime clock to its owned lab host."""

import argparse
import json
import math
import socket
import subprocess
import time


def verify_identity(site, hostname, addresses):
    if site not in (1, 2) or hostname != f"sdw-edge-{'a' if site == 1 else 'b'}":
        raise ValueError("Unexpected lab edge identity")
    nic = [row for row in addresses if row.get("ifname") == "eth0"]
    if (
        len(nic) != 1
        or nic[0].get("address") != f"52:54:00:53:0{site}:00"
        or not any(
            row.get("local") == f"192.168.56.{10 + site}" for row in nic[0].get("addr_info", [])
        )
    ):
        raise ValueError("Unexpected management NIC; clock was not changed")


def synchronize(site, host_epoch, *, apply=False, force=False):
    if not math.isfinite(host_epoch) or host_epoch <= 0:
        raise ValueError("Invalid host realtime clock")
    result = subprocess.run(
        ["ip", "-json", "address", "show"], capture_output=True, text=True, check=True, timeout=3
    )
    verify_identity(site, socket.gethostname(), json.loads(result.stdout))
    before = time.time()
    changed = apply and (force or abs(before - host_epoch) > 1)
    if changed:
        time.clock_settime(time.CLOCK_REALTIME, host_epoch)
    after = time.time()
    return {
        "site": site,
        "changed": changed,
        "guest_before_epoch": before,
        "host_request_epoch": host_epoch,
        "guest_after_epoch": after,
        "before_offset_seconds": before - host_epoch,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=int, choices=(1, 2), required=True)
    parser.add_argument("--host-epoch", type=float, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-sync", action="store_true")
    args = parser.parse_args()
    if args.force_sync and not args.apply:
        parser.error("--force-sync requires --apply")
    print(
        json.dumps(synchronize(args.site, args.host_epoch, apply=args.apply, force=args.force_sync))
    )

"""Trust the two lab SSH host keys only after checking their VM console fingerprints."""

import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verified_entry(scan, console, hostname, address):
    entries = [line.strip() for line in scan.splitlines() if line and not line.startswith("#")]
    if len(entries) != 1:
        raise RuntimeError("Expected exactly one lab SSH ed25519 host key")
    fields = entries[0].split()
    if len(fields) != 3 or fields[:2] != [address, "ssh-ed25519"]:
        raise RuntimeError("SSH host key belongs to an unexpected address or key type")
    try:
        key = base64.b64decode(fields[2], validate=True)
    except ValueError as error:
        raise RuntimeError("Invalid SSH host key encoding") from error
    fingerprint = base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
    observed = re.findall(
        r"\bSHA256:([A-Za-z0-9+/]{43})\s+root@" + re.escape(hostname) + r"\b", console
    )
    if fingerprint not in observed:
        raise RuntimeError("SSH host key does not match the fingerprint from its VM console")
    return entries[0], "SHA256:" + fingerprint


def main():
    runtime = ROOT / "lab/runtime"
    destination = runtime / "known_hosts"
    saved = destination.read_text() if destination.exists() else ""
    additions, evidence = [], []
    for site, side in ((1, "a"), (2, "b")):
        hostname, address = f"sdw-edge-{side}", f"192.168.56.{10 + site}"
        console = (runtime / f"edge-{side}/console.log").read_text(errors="replace")
        scan = subprocess.check_output(
            ["ssh-keyscan", "-T", "5", "-t", "ed25519", address],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        entry, fingerprint = verified_entry(scan, console, hostname, address)
        existing = [line for line in saved.splitlines() if line.split()[:1] == [address]]
        if existing and existing != [entry]:
            raise RuntimeError("A saved lab SSH host key differs; known_hosts was preserved")
        if not existing:
            additions.append(entry)
        evidence.append({"hostname": hostname, "address": address, "fingerprint": fingerprint})
    content = saved + ("\n" if saved and not saved.endswith("\n") else "")
    if additions:
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(content + "\n".join(additions) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    report = {
        "timestamp": time.time(),
        "verification": "VM console SHA256 fingerprint",
        "hosts": evidence,
    }
    (ROOT / "reports/flexiwan/ssh-hostkeys.json").write_text(json.dumps(report, indent=2))
    print("Verified both lab SSH host keys against their VM console fingerprints.")


if __name__ == "__main__":
    main()

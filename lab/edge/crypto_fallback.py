#!/usr/bin/env python3
"""Use VPP's OpenSSL crypto engine in a lab VM whose native engine crashes."""

import argparse
import os
import re
from pathlib import Path

BLOCK = """plugins {
    plugin crypto_native_plugin.so { disable }
    plugin crypto_ipsecmb_plugin.so { disable }
}
"""


def configure(text):
    known = re.search(
        r"(?m)^\s*plugins\s*\{\s*"
        r"plugin\s+crypto_native_plugin\.so\s*\{\s*disable\s*\}\s*"
        r"plugin\s+crypto_ipsecmb_plugin\.so\s*\{\s*disable\s*\}\s*\}",
        text,
    )
    if known and len(re.findall(r"(?m)^\s*plugins\s*\{", text)) == 1:
        return text
    if re.search(r"(?m)^\s*plugins\s*\{", text):
        raise RuntimeError("Existing plugin configuration requires review; it was preserved")
    return text.rstrip() + "\n\n" + BLOCK


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/vpp/startup.conf")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = Path(args.config)
    original = path.read_text()
    updated = configure(original)
    if args.apply:
        if os.geteuid() != 0:
            raise RuntimeError("Run as root inside the isolated lab edge VM")
        backup = path.with_name(path.name + ".ai-sdwan-before-crypto")
        if not backup.exists():
            backup.write_text(original)
            os.chmod(backup, 0o600)
        temporary = path.with_name(path.name + ".ai-sdwan-tmp")
        temporary.write_text(updated)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    print(
        "OpenSSL crypto fallback "
        + ("configured" if args.apply else "reviewed")
        + "; VPP restart required."
    )


if __name__ == "__main__":
    main()

import base64
import hashlib

import pytest

from scripts.verify_edge_hostkeys import verified_entry


@pytest.fixture
def host_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    )
    encoded = public.decode().split()[1]
    fingerprint = (
        base64.b64encode(hashlib.sha256(base64.b64decode(encoded)).digest()).decode().rstrip("=")
    )
    return f"192.168.56.11 ssh-ed25519 {encoded}", f"SHA256:{fingerprint} root@sdw-edge-a"


def test_host_key_requires_its_trusted_console_fingerprint(host_key):
    entry, console = host_key
    verified, fingerprint = verified_entry(entry, console, "sdw-edge-a", "192.168.56.11")
    assert verified == entry and fingerprint == console.split()[0]
    with pytest.raises(RuntimeError, match="VM console"):
        verified_entry(
            entry, "SHA256:" + "A" * 43 + " root@sdw-edge-a", "sdw-edge-a", "192.168.56.11"
        )


def test_another_vm_console_or_scan_address_cannot_be_trusted(host_key):
    entry, console = host_key
    with pytest.raises(RuntimeError, match="VM console"):
        verified_entry(
            entry, console.replace("sdw-edge-a", "sdw-edge-b"), "sdw-edge-a", "192.168.56.11"
        )
    with pytest.raises(RuntimeError, match="unexpected address"):
        verified_entry(
            entry.replace("192.168.56.11", "192.168.56.12"), console, "sdw-edge-a", "192.168.56.11"
        )

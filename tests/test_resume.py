from pathlib import PurePosixPath

import pytest

from lab import resume


def saved_vm():
    interfaces = "".join(
        f'<interface type="network"><source network="{network}"/>'
        f'<mac address="52:54:00:53:01:0{nic}"/><model type="virtio"/></interface>'
        for nic, network in enumerate(
            ("sdw-mgmt", "sdw-wan1a", "sdw-wan2a", "sdw-wan3a", "sdw-lan-a")
        )
    )
    return (
        '<domain type="kvm"><name>sdw-edge-a</name><devices>'
        '<disk><source file="/lab/lab/runtime/edge-a/disk.qcow2"/></disk>'
        '<disk><source file="/lab/lab/runtime/edge-a/seed.iso"/></disk>'
        + interfaces
        + "</devices></domain>"
    )


def test_resume_recognizes_only_the_existing_lab_vm_disks_and_five_nics():
    assert resume.vm_matches(saved_vm(), "a", PurePosixPath("/lab"))


@pytest.mark.parametrize(
    "before,after",
    [
        ("sdw-wan1a", "production-network"),
        ("52:54:00:53:01:01", "52:54:00:53:02:01"),
        ("edge-a/disk.qcow2", "another-vm/disk.qcow2"),
    ],
)
def test_foreign_network_mac_or_disk_prevents_start(before, after):
    assert not resume.vm_matches(saved_vm().replace(before, after), "a", PurePosixPath("/lab"))


def test_partial_namespace_resume_preserves_resources(monkeypatch):
    monkeypatch.setattr(resume, "output", lambda *args: "sdw-wan1 (id: 1)\n")
    with pytest.raises(RuntimeError, match="Partial lab namespaces were preserved"):
        resume.verified_namespaces()

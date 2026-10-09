"""Capture parser fixtures; only actual lab files can provide forwarding evidence."""

import struct

import pytest

from lab.analyze_paths import pcap_frames


def fixture(path, *, nano=False, vlan=False, truncate=False):
    magic = 0xA1B23C4D if nano else 0xA1B2C3D4
    header = struct.pack("<IHHIIII", magic, 2, 4, 0, 0, 160, 1)
    ether = bytes(12) + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    ipv4 = b"\x45" + bytes(8) + b"\x11" + bytes(2) + bytes((172, 16, 1, 1, 172, 16, 1, 2))
    frame = ether + ipv4 + bytes(10)
    record = struct.pack("<IIII", 1000, 250_000_000 if nano else 250_000, len(frame), 1436)
    path.write_bytes(header + record + (frame[:-1] if truncate else frame))


@pytest.mark.parametrize("nano,vlan", [(False, False), (True, False), (False, True)])
def test_classic_capture_timestamps_and_direction_work_with_vlan(tmp_path, nano, vlan):
    path = tmp_path / "capture.pcap"
    fixture(path, nano=nano, vlan=vlan)
    rows = list(pcap_frames(path))
    assert len(rows) == 1
    assert rows[0]["timestamp"] == 1000.25
    assert rows[0]["source"] == "172.16.1.1" and rows[0]["destination"] == "172.16.1.2"
    assert rows[0]["wire_bytes"] == 1436 and rows[0]["ip_protocol"] == 17


def test_empty_capture_has_no_frames_and_truncation_is_an_error(tmp_path):
    path = tmp_path / "capture.pcap"
    fixture(path)
    path.write_bytes(path.read_bytes()[:24])
    assert list(pcap_frames(path)) == []
    fixture(path, truncate=True)
    with pytest.raises(ValueError, match="Truncated captured frame"):
        list(pcap_frames(path))

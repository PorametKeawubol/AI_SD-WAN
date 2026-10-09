import json
import socket
import threading

import pytest

from lab.impairments import CAPACITY, DOWN, plan
from lab.linux_topology import network_matches, network_xml
from lab.run_live_eval import BASELINE_SECONDS, EVENT_END, TRIAL_SECONDS, assess
from lab.traffic import PACKET_BYTES, probe
from sdwan.flexiwan import IntegrationError


def test_fault_catalogue_covers_seven_down_sets_and_sixteen_cases():
    assert len({tuple(sorted(value)) for value in DOWN.values()}) == 7
    for index in range(1, 17):
        values = plan(f"S{index:02d}")
        assert set(values["wans"]) == set(CAPACITY)
        assert values["measurement"] == "real_netem_and_packets"
    assert all(row["loss_pct"] == 100 for row in plan("S07")["wans"].values())
    assert plan("S15")["telemetry_fault"] == "missing"
    assert plan("S16")["telemetry_fault"] == "corrupt"
    assert plan("S11")["background_load_mbps"] == 98


def test_progressive_fault_and_flap_are_time_dependent():
    assert plan("S12", 60)["wans"]["wan1"]["delay_ms"] > plan("S12", 0)["wans"]["wan1"]["delay_ms"]
    assert plan("S13", 0)["wans"]["wan1"]["loss_pct"] == 100
    assert plan("S13", 15)["wans"]["wan1"]["loss_pct"] == 0
    assert "<forward" not in network_xml("sdw-wan1a", "sdw-w1a")


def test_existing_network_reuse_requires_lab_identity_and_matching_subnet():
    value = network_xml("sdw-mgmt", "sdw-mgmt", "192.168.56.1")
    assert network_matches(value, value)
    assert not network_matches(value.replace("192.168.56.1", "192.168.70.1"), value)
    assert not network_matches(value.replace("<uuid>", "<uuid>wrong-"), value)


def test_libvirt_formatted_network_retains_the_saved_lab_identity():
    value = network_xml("sdw-mgmt", "sdw-mgmt", "192.168.56.1")
    actual = """<network>
  <name>sdw-mgmt</name>
  <uuid>dd2e225c-da41-5b32-b134-18065327dda9</uuid>
  <forward mode='nat'/>
  <bridge name='sdw-mgmt' stp='off' delay='0'/>
  <mac address='52:54:00:2d:34:e8'/>
  <ip address='192.168.56.1' netmask='255.255.255.0'>
  </ip>
</network>"""
    assert network_matches(actual, value)
    assert not network_matches(actual.replace("mode='nat'", "mode='route'"), value)
    assert not network_matches(actual.replace("name='sdw-mgmt'", "name='another-bridge'"), value)
    assert not network_matches(actual.replace("255.255.255.0", "255.255.0.0"), value)


def test_live_metrics_distinguish_all_down_from_restoration_and_invalid_harness():
    from sdwan.domain import PROFILES

    packets = [
        {
            "second": second,
            "offered_mbps": 2,
            "received_mbps": 2,
            "received_packets": 200,
            "latency_ms": 20,
            "jitter_ms": 3,
            "loss_pct": 0,
        }
        for second in range(TRIAL_SECONDS)
    ]
    assert assess(packets, [], "S01", PROFILES["voice"])["passed"]
    down = [
        {**row, "received_mbps": 0, "received_packets": 0, "loss_pct": 100}
        if BASELINE_SECONDS <= row["second"] < EVENT_END
        else row
        for row in packets
    ]
    states = [{"second": BASELINE_SECONDS + 4, "state": {"last_decision": {"action": "no_path"}}}]
    result = assess(down, states, "S07", PROFILES["voice"])
    assert result["passed"] and not result["restored"]
    assert not assess(down, [], "S07", PROFILES["voice"])["passed"]
    insufficient = [{**row, "offered_mbps": 0.1} for row in packets]
    result = assess(insufficient, [], "S01", PROFILES["voice"])
    assert not result["valid"] and not result["passed"]


def test_packet_measurement_uses_actual_udp_and_reports_missing_echo(tmp_path):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(0.2)
    port = server.getsockname()[1]
    stopping = threading.Event()

    def serve():
        while not stopping.is_set():
            try:
                packet, address = server.recvfrom(65535)
                assert len(packet) == PACKET_BYTES
                server.sendto(packet, address)
            except TimeoutError:
                continue

    thread = threading.Thread(target=serve)
    thread.start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        source_port = reservation.getsockname()[1]
    try:
        rows = probe("127.0.0.1", port, 1, 0.1, tmp_path / "echo.json", source_port=source_port)
        assert rows[0]["received_packets"] > 0
        assert rows[0]["received_mbps"] > 0
        assert rows[0]["loss_pct"] == 0
        document = json.loads((tmp_path / "echo.json").read_text())
        assert document["source_ip"] == "127.0.0.1"
        assert document["source_port"] == source_port
        assert document["started_at"] > 0
        assert document["destination_port"] == port
    finally:
        stopping.set()
        thread.join(1)
        server.close()
    rows = probe("127.0.0.1", port, 1, 0.1, tmp_path / "no-echo.json")
    assert rows[0]["loss_pct"] == 100 and rows[0]["received_mbps"] == 0


def test_journal_writer_lock_excludes_a_second_writer(lab):
    with lab.transaction(), pytest.raises(IntegrationError, match="writer lock"), lab.transaction():
        pytest.fail("A second writer acquired the same transaction")


@pytest.fixture
def lab(tmp_path):
    from sdwan.flexiwan import ApplyJournal

    return ApplyJournal(tmp_path / "journal")

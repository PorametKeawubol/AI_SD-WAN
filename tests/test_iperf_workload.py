"""No network mutation: scoped argv and receiver-only iperf result fixtures."""

import json
import shutil
import socket
import subprocess
import time

import pytest

from lab.iperf_workload import (
    background_client_command,
    background_server_command,
    parse_iperf_json,
)


@pytest.mark.parametrize("link,index,capacity", [("wan1", 1, 100), ("wan2", 2, 80), ("wan3", 3, 30)])
@pytest.mark.parametrize("side,site", [("a", 101), ("b", 102)])
def test_background_is_pinned_to_exact_wan_and_bound_receiver(link, index, capacity, side, site):
    command = background_client_command(link, side, capacity * 1.1, 60)
    assert command[:4] == ["ip", "netns", "exec", "sdw-" + link]
    assert command[command.index("-c") + 1] == f"172.16.{index}.{site}"
    assert command[command.index("-B") + 1] == f"172.16.{index}.254"
    assert command[command.index("-p") + 1] == str(5310 + index)
    assert command[command.index("-l") + 1] == "1200"
    assert "--get-server-output" in command and "-J" in command and "-u" in command
    assert command[command.index("--connect-timeout") + 1] == "3000"
    assert background_server_command(link, side) == [
        "iperf3", "-s", "-B", f"172.16.{index}.{site}", "-p", str(5310 + index), "-J"
    ]


@pytest.mark.parametrize("link,side", [("wan4", "a"), ("wan1;id", "a"), ("wan1", "c")])
def test_unknown_scope_rejected(link, side):
    with pytest.raises(ValueError, match="owned WAN"):
        background_client_command(link, side, 10, 60)


@pytest.mark.parametrize("rate", [0, -1, 110.01, float("nan"), float("inf"), True, "10"])
def test_invalid_rate_rejected(rate):
    with pytest.raises(ValueError, match="Offered rate"):
        background_client_command("wan1", "a", rate, 60)


@pytest.mark.parametrize("duration", [0, -1, 600.01, float("nan"), True, "60"])
def test_invalid_duration_rejected(duration):
    with pytest.raises(ValueError, match="Duration"):
        background_client_command("wan1", "a", 10, duration)


def receiver(**changes):
    return {
        "seconds": 10,
        "bytes": 75000000,
        "bits_per_second": 60000000,
        "sender": False,
        "jitter_ms": 2.4,
        "lost_packets": 100,
        "packets": 1000,
        "lost_percent": 10,
        **changes,
    }


def test_older_udp_server_receiver_preferred_over_client_sender():
    document = {
        "end": {"sum": receiver(bits_per_second=90000000, sender=True, lost_percent=0)},
        "server_output_json": {"end": {"sum": receiver()}},
    }
    result = parse_iperf_json(document, requested_mbps=95)
    assert result["ok"] and result["receiver_available"]
    assert result["requested_mbps"] == 95
    assert result["sender_mbps"] == 90
    assert result["received_mbps"] == 60
    assert result["loss_pct"] == 10 and result["jitter_ms"] == 2.4
    assert result["receiver_source"] == "server_output_json.end.sum"


def test_newer_client_explicit_receiver_supported_without_server_output():
    result = parse_iperf_json(json.dumps({"end": {"sum_received": receiver()}}))
    assert result["ok"] and result["received_mbps"] == 60
    assert result["receiver_source"] == "end.sum_received"


def test_server_context_supports_older_udp_record_without_sender_flag():
    record = receiver()
    del record["sender"]
    result = parse_iperf_json({"server_output_json": json.dumps({"end": {"sum": record}})})
    assert result["ok"] and result["received_mbps"] == 60


@pytest.mark.parametrize("end", [
    {"sum": receiver(sender=True)},
    {"sum_received": receiver(sender=True)},
    {"sum": {key: value for key, value in receiver().items() if key != "sender"}},
    {"streams": [{"udp": receiver(sender=True)}]},
])
def test_sender_or_ambiguous_client_data_never_becomes_receiver(end):
    result = parse_iperf_json({"end": end}, requested_mbps=70)
    assert not result["ok"] and not result["receiver_available"]
    assert result["received_mbps"] is None and result["loss_pct"] is None


def test_zero_received_rate_is_a_valid_measured_result():
    result = parse_iperf_json({"end": {"sum_received": receiver(bits_per_second=0, bytes=0)}})
    assert result["ok"] and result["received_mbps"] == 0


@pytest.mark.parametrize("changes", [{"seconds": 0}, {"bits_per_second": float("nan")}, {"bytes": -1}])
def test_invalid_receiver_metrics_remain_unavailable(changes):
    result = parse_iperf_json({"end": {"sum_received": receiver(**changes)}})
    assert not result["ok"] and result["received_mbps"] is None


def test_tcp_receiver_does_not_invent_udp_loss_or_jitter():
    result = parse_iperf_json({"end": {"sum_received": {
        "seconds": 10, "bytes": 75000000, "bits_per_second": 60000000, "sender": False,
    }}})
    assert result["ok"] and result["loss_pct"] is None and result["jitter_ms"] is None


@pytest.mark.parametrize("document", [
    "not json", [], {"end": []}, {"error": "unable to connect to server"},
    {"server_output_json": {"error": "test failed"}},
])
def test_missing_or_failed_receiver_is_explicit(document):
    result = parse_iperf_json(document)
    assert not result["ok"] and result["error"] and result["received_mbps"] is None


def test_one_udp_receiver_stream_supported_but_unexpected_parallel_streams_not_summed():
    assert parse_iperf_json({"end": {"streams": [{"udp": receiver()}]}})["ok"]
    result = parse_iperf_json({"end": {"streams": [{"udp": receiver()}, {"udp": receiver()}]}})
    assert not result["ok"] and result["received_mbps"] is None


def test_installed_iperf_accepts_workload_flags_and_reports_a_real_receiver():
    """A loopback tool check only: never claims WAN forwarding or performance.

    Execute the generated sender flags against a real installed iperf3. Only
    namespace scope and endpoint addresses/port change for this isolated test.
    This catches flags that parse as argv but the executable rejects by role.
    """
    executable = shutil.which("iperf3")
    if executable is None:
        pytest.skip("iperf3 is not installed on this test host")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]

    def loopback(command):
        command[0] = executable
        for option in ("-c", "-B"):
            if option in command:
                command[command.index(option) + 1] = "127.0.0.1"
        command[command.index("-p") + 1] = str(port)
        return command

    server = subprocess.Popen(
        loopback(background_server_command("wan3", "b")),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            if server.poll() is not None:
                pytest.fail("The installed iperf3 receiver did not start")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if time.monotonic() >= deadline:
                pytest.fail("The installed iperf3 receiver did not become ready")
            time.sleep(0.05)
        # The readiness connection carries no iperf control protocol. Let the
        # persistent server finish rejecting it before the actual measurement.
        time.sleep(0.1)
        command = loopback(background_client_command("wan3", "b", 1, 1)[4:])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        assert completed.returncode == 0, completed.stderr
        result = parse_iperf_json(completed.stdout, requested_mbps=1)
        assert result["ok"], result["error"]
        assert result["receiver_available"] and result["receiver_source"]
        assert result["received_bytes"] > 0 and result["received_mbps"] > 0
        assert result["loss_pct"] is not None and result["jitter_ms"] is not None
    finally:
        if server.poll() is None:
            server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=5)

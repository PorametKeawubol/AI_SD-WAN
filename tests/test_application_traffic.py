"""Real loopback HTTP measurements; these do not prove SD-WAN forwarding."""

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lab import application_traffic as traffic
from lab import traffic as udp


@pytest.fixture
def server():
    with traffic.create_server("127.0.0.1", 0) as instance:
        worker = threading.Thread(target=instance.serve_forever)
        worker.start()
        try:
            yield instance.server_address
        finally:
            instance.shutdown()
            worker.join()


def test_real_http_counts_received_bytes_and_latency(server):
    for workload, size in (("small", len(traffic.SMALL_BODY)), ("download", 150003)):
        row = traffic.measure_request(*server, workload, download_bytes=size)
        assert row["success"] is True and row["error"] is None
        assert row["received_bytes"] == row["requested_bytes"] == size
        assert 0 < row["response_latency_ms"] <= row["completion_latency_ms"]
        assert row["goodput_mbps"] == pytest.approx(size * 8 / row["elapsed_ms"] / 1000)
        assert row["finished_at"] >= row["timestamp"]


def test_readiness_waits_for_real_delayed_server():
    with traffic.create_server("127.0.0.1", 0) as instance:
        started = threading.Event()

        def delayed_server():
            time.sleep(0.1)
            started.set()
            instance.serve_forever()

        worker = threading.Thread(target=delayed_server)
        worker.start()
        try:
            before = time.perf_counter()
            assert traffic.wait_for_http(*instance.server_address, timeout=1)
            assert started.is_set()
            assert 0.08 <= time.perf_counter() - before < 1
        finally:
            started.wait(timeout=1)
            instance.shutdown()
            worker.join()


def test_readiness_rejects_a_different_service_without_waiting_for_timeout():
    class OtherHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b'{"status":"ok"}\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), OtherHandler) as instance:
        worker = threading.Thread(target=instance.serve_forever)
        worker.start()
        try:
            before = time.perf_counter()
            with pytest.raises(RuntimeError, match="unexpected service"):
                traffic.wait_for_http(*instance.server_address, timeout=1)
            assert time.perf_counter() - before < 0.5
        finally:
            instance.shutdown()
            worker.join()


def test_readiness_refused_connection_stops_at_overall_deadline():
    with traffic.create_server("127.0.0.1", 0) as instance:
        endpoint = instance.server_address
    before = time.perf_counter()
    with pytest.raises(RuntimeError, match="before the deadline"):
        traffic.wait_for_http(*endpoint, timeout=0.1)
    assert time.perf_counter() - before < 0.3


def test_server_returns_deterministic_bytes_and_rejects_other_paths(server):
    connection = http.client.HTTPConnection(*server)
    connection.request("GET", "/download?bytes=257")
    response = connection.getresponse()
    assert response.read() == bytes(range(256)) + b"\x00"
    connection.close()
    for path in (
        "/../../pyproject.toml",
        "/download?bytes=0",
        f"/download?bytes={traffic.MAX_DOWNLOAD_BYTES + 1}",
        "/download?bytes=1&bytes=2",
        "/download?bytes=100&file=private.json",
        "/download?bytes=",
    ):
        connection = http.client.HTTPConnection(*server)
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status in (400, 404)
        response.read()
        connection.close()


def test_connection_failure_is_not_a_healthy_zero_measurement():
    with traffic.create_server("127.0.0.1", 0) as closed:
        endpoint = closed.server_address
    row = traffic.measure_request(*endpoint, "download", timeout=0.1)
    assert row["success"] is False
    assert row["error"] and row["received_bytes"] == 0
    assert row["response_latency_ms"] is None
    assert row["completion_latency_ms"] is None
    assert row["goodput_mbps"] is None


def test_partial_download_records_actual_bytes_and_times_out():
    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"abc")
            self.wfile.flush()
            time.sleep(0.3)

    with ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler) as instance:
        worker = threading.Thread(target=instance.serve_forever)
        worker.start()
        try:
            row = traffic.measure_request(
                *instance.server_address, "download", download_bytes=100, timeout=0.1
            )
        finally:
            instance.shutdown()
            worker.join()
    assert row["success"] is False and row["error"] == "TimeoutError"
    assert row["received_bytes"] == 3
    assert row["response_latency_ms"] is not None
    assert row["completion_latency_ms"] is None and row["goodput_mbps"] is None
    assert 80 <= row["elapsed_ms"] < 250


def test_client_streams_both_workloads_and_finishes_atomic_latest(server, tmp_path):
    output, latest = tmp_path / "samples.jsonl", tmp_path / "latest.json"
    summary = traffic.run_client(
        *server,
        1,
        output,
        interval=0.2,
        download_interval=1,
        download_bytes=65537,
        latest=latest,
    )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert {row["workload"] for row in rows} == {"small", "download"}
    assert all(row["success"] for row in rows)
    assert {row["application"] for row in rows} == {"http", "download"}
    assert len(rows) >= 4
    assert summary == json.loads(latest.read_text())
    assert summary["active"] is False
    assert sum(item["requests"] for item in summary["counts"].values()) == len(rows)
    assert not list(tmp_path.glob(".latest.json.*"))


@pytest.mark.parametrize(
    "option,value",
    [
        ("duration", 0),
        ("duration", 601),
        ("duration", float("nan")),
        ("interval", 0),
        ("interval", float("inf")),
        ("download_interval", 0.1),
        ("download_bytes", 0),
        ("download_bytes", traffic.MAX_DOWNLOAD_BYTES + 1),
        ("download_bytes", True),
        ("timeout", 0),
        ("timeout", 31),
    ],
)
def test_client_refuses_unbounded_workloads(tmp_path, option, value):
    arguments = {"duration": 1, "output": tmp_path / "unused.jsonl", option: value}
    with pytest.raises(ValueError):
        traffic.run_client("127.0.0.1", 8088, **arguments)
    assert not arguments["output"].exists()


def test_client_cannot_replace_its_jsonl_with_latest_json(tmp_path):
    output = tmp_path / "same.json"
    with pytest.raises(ValueError, match="paths must differ"):
        traffic.run_client("127.0.0.1", 8088, 1, output, latest=output)


def test_measurement_writer_failure_is_propagated(server, tmp_path, monkeypatch):
    def failed_write(*_args):
        raise OSError("Fixture filesystem failure")

    monkeypatch.setattr(traffic, "_atomic_json", failed_write)
    with pytest.raises(RuntimeError, match="worker stopped"):
        traffic.run_client(*server, 1, tmp_path / "samples.jsonl", latest=tmp_path / "latest.json")


def test_host_rejects_url_and_hostname():
    for host in ("http://127.0.0.1", "localhost", "::1"):
        with pytest.raises(ValueError):
            traffic.create_server(host, 0)


def test_udp_interval_stream_is_incremental_and_matches_final_packets(tmp_path):
    stopped = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.05)

        def echo():
            while not stopped.is_set():
                try:
                    packet, address = receiver.recvfrom(65535)
                except TimeoutError:
                    continue
                receiver.sendto(packet, address)

        echo_worker = threading.Thread(target=echo)
        echo_worker.start()
        packets, intervals = tmp_path / "packets.json", tmp_path / "intervals.jsonl"
        probe_worker = threading.Thread(
            target=udp.probe,
            args=(*receiver.getsockname(), 3, 0.1, packets),
            kwargs={"interval_output": intervals},
        )
        probe_worker.start()
        try:
            deadline = time.perf_counter() + 3
            while time.perf_counter() < deadline:
                if intervals.exists() and intervals.read_text():
                    break
                time.sleep(0.02)
            assert intervals.read_text()
            assert probe_worker.is_alive(), "First interval must arrive before run completion"
            probe_worker.join(timeout=5)
            assert not probe_worker.is_alive()
        finally:
            stopped.set()
            echo_worker.join()
            probe_worker.join(timeout=5)
    document = json.loads(packets.read_text())
    rows = [json.loads(line) for line in intervals.read_text().splitlines()]
    assert [row["second"] for row in rows] == [0, 1, 2]
    for row, legacy in zip(rows, document["intervals"], strict=True):
        assert row["timestamp"] == document["started_at"] + row["second"]
        assert row["application"] == "udp" and row["success"]
        assert row["received_packets"] == legacy["received_packets"] > 0
        assert row["loss_pct"] == legacy["loss_pct"]
        assert row["latency_ms"] == legacy["latency_ms"]


def test_udp_no_echo_has_null_latency_and_raw_full_loss(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blackhole:
        blackhole.bind(("127.0.0.1", 0))
        packets, intervals = tmp_path / "packets.json", tmp_path / "intervals.jsonl"
        legacy = udp.probe(*blackhole.getsockname(), 1, 0.1, packets, interval_output=intervals)
    row = json.loads(intervals.read_text())
    assert row["success"] is False and row["sent_packets"] > 0
    assert row["received_packets"] == 0 and row["loss_pct"] == 100
    assert row["latency_ms"] is None and row["jitter_ms"] is None
    assert legacy[0]["latency_ms"] == 10000  # Existing packets.json remains compatible.

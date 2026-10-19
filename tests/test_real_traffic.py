"""Workload boundaries, incremental evidence and owned receiver cleanup.

These fixtures never touch namespaces, controllers, qdiscs or SSH.
"""

import json
from types import SimpleNamespace

import pytest

from lab import real_traffic
from lab.iperf_workload import background_client_command


def test_capacity_only_plan_cannot_inject_quality_metrics():
    plan = real_traffic.capacity_only()
    assert set(plan["wans"]) == {"wan1", "wan2", "wan3"}
    capacities = {"wan1": 100, "wan2": 80, "wan3": 30}
    for link, config in plan["wans"].items():
        assert config["rate_mbps"] == capacities[link]
        assert config["delay_ms"] == config["jitter_ms"] == config["loss_pct"] == 0
        assert "burst_loss" not in config


def test_cycle_congests_one_wan_with_actual_load_and_recovers_without_quality_injection():
    samples = [real_traffic.workload_plan(second) for second in (0, 59.99, 60, 149.99, 150, 209.99, 210)]
    assert [sample["phase"] for sample in samples] == [
        "baseline", "baseline", "wan1_busy", "wan1_busy", "recovery", "recovery", "baseline"
    ]
    baseline = samples[0]["per_direction_requested_mbps"]
    busy = samples[2]["per_direction_requested_mbps"]
    recovered = samples[4]["per_direction_requested_mbps"]
    assert recovered == baseline
    assert busy["wan1"] > 100
    assert busy["wan2"] == baseline["wan2"] and busy["wan3"] == baseline["wan3"]
    for sample in samples:
        assert set(sample) == {"phase", "cycle", "per_direction_requested_mbps"}
        for link, capacity in {"wan1": 100, "wan2": 80, "wan3": 30}.items():
            rate = sample["per_direction_requested_mbps"][link]
            assert 0 < rate <= capacity * 1.1
            # The upper bound is representable by the actual argv generator.
            command = background_client_command(link, "b", rate, 10)
            assert float(command[command.index("-b") + 1].removesuffix("M")) <= capacity * 1.1
    assert samples[-1]["cycle"] == 1


@pytest.mark.parametrize("elapsed", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_workload_time_rejected(elapsed):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        real_traffic.workload_plan(elapsed)


def test_jsonl_partial_line_waits_for_completion_without_replaying_previous_evidence(tmp_path):
    path = tmp_path / "measurement.jsonl"
    first = json.dumps({"timestamp": 1, "application": "download", "note": "รับข้อมูล"}, ensure_ascii=False).encode("utf-8") + b"\n"
    second = json.dumps({"timestamp": 2, "application": "http"}).encode("utf-8") + b"\n"
    cut = len(second) // 2
    path.write_bytes(first + second[:cut])
    rows, offset = real_traffic.drain_jsonl(path, 0)
    assert rows == [{"timestamp": 1, "application": "download", "note": "รับข้อมูล"}]
    assert offset == len(first)  # Byte offset, independent of UTF-8 character count.
    assert real_traffic.drain_jsonl(path, offset) == ([], offset)
    with path.open("ab") as handle:
        handle.write(second[cut:])
    rows, completed_offset = real_traffic.drain_jsonl(path, offset)
    assert rows == [{"timestamp": 2, "application": "http"}]
    assert completed_offset == len(first + second)
    assert real_traffic.drain_jsonl(path, completed_offset) == ([], completed_offset)


def test_jsonl_missing_file_and_incomplete_first_line_are_unavailable(tmp_path):
    path = tmp_path / "measurement.jsonl"
    assert real_traffic.drain_jsonl(path, 27) == ([], 27)
    path.write_bytes(b'{"timestamp": 1}')
    assert real_traffic.drain_jsonl(path, 0) == ([], 0)


def test_jsonl_malformed_complete_evidence_is_not_silently_discarded(tmp_path):
    path = tmp_path / "measurement.jsonl"
    path.write_bytes(b'{"timestamp": invalid}\n')
    with pytest.raises(json.JSONDecodeError):
        real_traffic.drain_jsonl(path, 0)


@pytest.fixture
def controlled_time(monkeypatch):
    clock = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(real_traffic.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(real_traffic.time, "sleep", sleep)
    return clock


class ReadOnlyHTTP:
    """Return real API-shaped states; reject any controller/API write."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def get(self, path):
        assert path == "/api/state"
        self.calls.append(("GET", path))
        state = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        response = SimpleNamespace(json=lambda: state)
        response.raise_for_status = lambda: response
        return response

    def post(self, *args, **kwargs):
        pytest.fail("Waiting for an apply must not write any configuration")


def test_wait_idle_uses_real_api_apply_inflight_before_allowing_policy_work(controlled_time):
    pending = {"apply_inflight": {"id": "existing-apply", "target": "wan2"}}
    idle = {"apply_inflight": None, "confirmed_path": "wan2"}
    http = ReadOnlyHTTP([pending, pending, pending, idle])

    assert real_traffic.wait_idle(http) == idle
    assert len(http.calls) == 4
    assert len(controlled_time["sleeps"]) == 3
    assert all(method == "GET" for method, _ in http.calls)


def test_wait_idle_cancellation_preserves_an_existing_apply(controlled_time):
    http = ReadOnlyHTTP([{"apply_inflight": {"id": "existing-apply", "target": "wan2"}}])

    with pytest.raises(RuntimeError, match="Stopped while waiting"):
        real_traffic.wait_idle(http, stopped=lambda: True)

    assert http.calls == [("GET", "/api/state")]
    assert controlled_time["sleeps"] == []


def test_wait_idle_timeout_never_writes_over_unfinished_apply(controlled_time):
    http = ReadOnlyHTTP([{"apply_inflight": {"id": "existing-apply", "target": "wan2"}}])

    with pytest.raises(RuntimeError, match="Existing apply did not finish; no policy update sent"):
        real_traffic.wait_idle(http)

    assert len(http.calls) > 1
    assert controlled_time["now"] >= 190
    assert all(method == "GET" for method, _ in http.calls)


def test_wait_receivers_requires_each_exact_namespace_and_bound_address(
    monkeypatch, controlled_time
):
    endpoints = [
        {"namespace": "sdw-load1a", "address": "172.16.1.101", "port": 5311},
        {"namespace": "sdw-load1b", "address": "172.16.1.102", "port": 5311},
    ]
    calls = []

    def listeners(command, *, text, timeout):
        namespace = command[3]
        binding = next(row for row in endpoints if row["namespace"] == namespace)
        assert command == [
            "ip", "netns", "exec", namespace, "ss", "-H", "-ltn", "sport = :5311"
        ]
        assert text is True and timeout == 3
        calls.append(namespace)
        # A wildcard socket is not proof that the receiver owns its exact IP.
        address = "0.0.0.0" if controlled_time["now"] == 0 else binding["address"]
        return f"LISTEN 0 4096 {address}:5311 0.0.0.0:*\n"

    monkeypatch.setattr(real_traffic.subprocess, "check_output", listeners)
    receivers = [SimpleNamespace(poll=lambda: None) for _ in endpoints]
    real_traffic.wait_receivers(endpoints, receivers)

    assert calls == ["sdw-load1a", "sdw-load1b", "sdw-load1a", "sdw-load1b"]
    assert controlled_time["sleeps"]


def test_wait_receivers_does_not_accept_a_dead_receiver(monkeypatch, controlled_time):
    def forbidden_probe(*args, **kwargs):
        pytest.fail("An exited receiver must fail before probing listener sockets")

    monkeypatch.setattr(real_traffic.subprocess, "check_output", forbidden_probe)
    with pytest.raises(RuntimeError, match="receiver failed during startup"):
        real_traffic.wait_receivers([], [SimpleNamespace(poll=lambda: 1)])


def test_wait_receivers_timeout_with_wrong_bind_address(monkeypatch, controlled_time):
    endpoint = {"namespace": "sdw-load1a", "address": "172.16.1.101", "port": 5311}
    monkeypatch.setattr(
        real_traffic.subprocess,
        "check_output",
        lambda *args, **kwargs: "LISTEN 0 4096 172.16.1.102:5311 0.0.0.0:*\n",
    )

    with pytest.raises(RuntimeError, match="Pinned receivers did not become ready"):
        real_traffic.wait_receivers([endpoint], [SimpleNamespace(poll=lambda: None)])
    assert controlled_time["now"] >= 15

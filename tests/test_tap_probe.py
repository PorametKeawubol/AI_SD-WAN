import json
import subprocess

import pytest

from lab.edge import probe
from lab.edge.probe import linux_ping_metrics, linux_tap_ping


def test_linux_echoes_measure_rtt_stddev_and_conservative_loss():
    result = linux_ping_metrics(
        "time=10 ms\ntime=30 ms\n3 packets transmitted, 2 received, 33.33% packet loss"
    )
    assert result[:3] == (True, 20, 10)
    assert result[3] == pytest.approx(100 / 3)
    assert linux_ping_metrics("100 packets transmitted, 0 received, 100% packet loss") == (
        False,
        10000,
        5000,
        100,
    )
    with pytest.raises(ValueError):
        linux_ping_metrics("time=10 ms\n3 packets transmitted, 2 received, 33.33% packet loss")
    with pytest.raises(ValueError):
        linux_ping_metrics("ping failed before producing a summary")


@pytest.mark.parametrize("kind,mac", [("veth", "52:54:00:53:01:01"), ("tun", "52:54:00:53:02:01")])
def test_probe_cannot_bypass_vpp_using_another_interface_or_mac(monkeypatch, kind, mac):
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                [{"address": mac, "linkinfo": {"info_kind": kind, "info_data": {"type": "tap"}}}]
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="verified VPP TAP"):
        linux_tap_ping(
            {"linux_interface": "eth1", "mac": "52:54:00:53:01:01", "target_ip": "172.16.1.2"}
        )
    assert len(calls) == 1


def test_measure_combines_tap_rtt_with_vpp_counters_and_omits_failed_path(monkeypatch):
    config = {
        "probe_method": "linux_tap",
        "links": {
            f"wan{i}": {
                "vpp_interface": f"GigabitEthernet0/{i}/0",
                "target_ip": f"172.16.{i}.2",
                "capacity_mbps": 100,
            }
            for i in range(1, 4)
        },
    }
    frames = iter(
        "\n".join(
            f"GigabitEthernet0/{i}/0 {i} up\n rx bytes {count}\n tx bytes {count}"
            for i in range(1, 4)
        )
        for count in (100, 125100)
    )
    ticks = iter((10.0, 11.0))

    def tap(binding):
        if binding["target_ip"] == "172.16.2.2":
            raise ValueError("Incomplete measurements")
        if binding["target_ip"] == "172.16.3.2":
            return False, 10000.0, 5000.0, 100.0
        return True, 20.0, 10.0, 2.0

    monkeypatch.setattr(probe, "command", lambda *args: next(frames))
    monkeypatch.setattr(probe, "linux_tap_ping", tap)
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    result = probe.measure(config)
    assert result["probe_origin"] == "linux_tap_through_vpp"
    samples = {row["link_id"]: row for row in result["samples"]}
    assert set(samples) == {"wan1", "wan3"}
    assert samples["wan1"]["up"] is True
    assert samples["wan1"]["latency_ms"] == 20
    assert samples["wan1"]["jitter_ms"] == 10
    assert samples["wan1"]["loss_pct"] == 2
    assert samples["wan1"]["utilization_pct"] == 1
    assert samples["wan3"]["up"] is False
    assert samples["wan3"]["loss_pct"] == 100

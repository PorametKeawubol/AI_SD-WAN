"""Guest clock identity fixtures; no live system clocks are changed here."""

from types import SimpleNamespace

import pytest

from lab.edge import clock
from lab.run_live_eval import verify_elapsed_clocks


def test_clock_correction_requires_exact_generated_guest_identity():
    rows = [
        {
            "ifname": "eth0",
            "address": "52:54:00:53:01:00",
            "addr_info": [{"local": "192.168.56.11"}],
        }
    ]
    clock.verify_identity(1, "sdw-edge-a", rows)
    with pytest.raises(ValueError, match="identity"):
        clock.verify_identity(1, "sdw-edge-b", rows)
    rows[0]["address"] = "52:54:00:00:00:00"
    with pytest.raises(ValueError, match="clock was not changed"):
        clock.verify_identity(1, "sdw-edge-a", rows)


def test_realtime_discontinuity_and_scheduler_gap_stop_a_trial(monkeypatch):
    import lab.run_live_eval as runner

    monkeypatch.setattr(runner.time, "monotonic", lambda: 110)
    monkeypatch.setattr(runner.time, "time", lambda: 1010)
    assert verify_elapsed_clocks(1000, 100, 5) == 10
    monkeypatch.setattr(runner.time, "time", lambda: 2000)
    with pytest.raises(RuntimeError, match="clock discontinuity"):
        verify_elapsed_clocks(1000, 100, 5)
    monkeypatch.setattr(runner.time, "time", lambda: 1010)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 113)
    with pytest.raises(RuntimeError, match="Host suspend"):
        verify_elapsed_clocks(1000, 100, 0)


def test_read_only_clock_inspection_does_not_set_time(monkeypatch):
    monkeypatch.setattr(clock, "verify_identity", lambda *args: None)
    monkeypatch.setattr(clock.socket, "gethostname", lambda: "sdw-edge-a")
    monkeypatch.setattr(
        clock.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="[]")
    )
    monkeypatch.setattr(clock.time, "time", lambda: 1000)
    monkeypatch.setattr(
        clock.time,
        "clock_settime",
        lambda *args: pytest.fail("Read-only changed the clock"),
        raising=False,
    )
    result = clock.synchronize(1, 2000)
    assert not result["changed"] and result["before_offset_seconds"] == -1000


def test_force_clock_sync_repairs_subsecond_skew_only_with_apply(monkeypatch):
    monkeypatch.setattr(clock, "verify_identity", lambda *args: None)
    monkeypatch.setattr(clock.socket, "gethostname", lambda: "sdw-edge-a")
    monkeypatch.setattr(
        clock.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="[]")
    )
    monkeypatch.setattr(clock.time, "time", lambda: 1000.1)
    changes = []
    monkeypatch.setattr(clock.time, "CLOCK_REALTIME", 0, raising=False)
    monkeypatch.setattr(
        clock.time, "clock_settime", lambda *args: changes.append(args), raising=False
    )
    assert not clock.synchronize(1, 1000, force=True)["changed"]
    assert not changes
    assert not clock.synchronize(1, 1000, apply=True)["changed"]
    assert not changes
    assert clock.synchronize(1, 1000, apply=True, force=True)["changed"]
    assert changes == [(clock.time.CLOCK_REALTIME, 1000)]

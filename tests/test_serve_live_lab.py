"""Regression checks for graceful live API shutdown; never signal real processes."""

import importlib.util
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def shutdown(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts/serve_live_lab.py"
    spec = importlib.util.spec_from_file_location("demo_live_launcher_test", source)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    (tmp_path / ".ai-sdwan-owned").touch()
    pid_file = tmp_path / "api.pid"
    pid_file.write_text("12345")
    launcher.ROOT = tmp_path
    launcher.PID_FILE = pid_file
    clock = SimpleNamespace(now=0, exits_at=25, foreign=False, stat_race=False, signals=[])

    def read_command():
        if clock.foreign:
            return b"python\0another_service.py\0"
        return b"python\0" + str(source).encode() + b"\0"

    def read_stat():
        if clock.stat_race and clock.now >= 1:
            raise FileNotFoundError("Process exited between checks")
        return b"12345 (python) S 0 0 0"

    def process_path(value):
        if str(value) == "/proc/12345/cmdline":
            return SimpleNamespace(
                exists=lambda: clock.exits_at is None or clock.now < clock.exits_at,
                read_bytes=read_command,
            )
        if str(value) == "/proc/12345/stat":
            return SimpleNamespace(read_bytes=read_stat)
        return Path(value)

    def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(launcher, "Path", process_path)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(launcher.time, "sleep", sleep)
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: clock.signals.append((pid, sig)))
    monkeypatch.setattr(launcher.os, "name", "posix")
    monkeypatch.setattr(sys, "argv", [str(source), "--stop"])
    return launcher, clock


def test_shutdown_waits_for_connections_beyond_old_twenty_second_limit(shutdown):
    launcher, clock = shutdown
    launcher.main()
    assert clock.now >= 25
    assert clock.signals == [(12345, signal.SIGTERM)]


def test_stuck_shutdown_times_out_without_force_kill(shutdown):
    launcher, clock = shutdown
    clock.exits_at = None
    with pytest.raises(RuntimeError, match="still shutting down"):
        launcher.main()
    assert clock.now >= 60
    assert clock.signals == [(12345, signal.SIGTERM)]


def test_foreign_process_is_preserved(shutdown):
    launcher, clock = shutdown
    clock.foreign = True
    with pytest.raises(RuntimeError, match="another process"):
        launcher.main()
    assert not clock.signals


def test_exit_between_proc_checks_counts_as_stopped(shutdown):
    launcher, clock = shutdown
    clock.exits_at = None
    clock.stat_race = True
    launcher.main()
    assert clock.now == 1
    assert clock.signals == [(12345, signal.SIGTERM)]


def test_already_stopped_does_not_signal(shutdown):
    launcher, clock = shutdown
    clock.exits_at = 0
    launcher.main()
    assert not clock.signals

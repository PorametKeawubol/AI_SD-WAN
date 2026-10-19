"""Keep the clock bound enforced while retrying slow SSH timestamp exchanges."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def clock_helper():
    source = Path(__file__).resolve().parents[1] / "scripts/sync_edge_clocks.py"
    spec = importlib.util.spec_from_file_location("demo_clock_sync_test", source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def test_slow_clock_exchange_retries_with_new_timestamp(monkeypatch, clock_helper):
    timestamps = iter([100, 102, 103, 103.3])
    monkeypatch.setattr(clock_helper.time, "time", lambda: next(timestamps))
    commands = []

    def response(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps({"guest_after_epoch": 100 if len(commands) == 1 else 103}))

    monkeypatch.setattr(clock_helper.subprocess, "run", response)
    result = clock_helper.sync_site(1, [], "test-code", apply=True, force_sync=True)
    assert result["synchronization_attempt"] == 2
    assert abs(result["after_offset_upper_bound_seconds"]) <= 1
    assert commands[0][commands[0].index("--host-epoch") + 1] == "100.000000"
    assert commands[1][commands[1].index("--host-epoch") + 1] == "103.000000"
    assert all("--apply" in command and "--force-sync" in command for command in commands)


def test_bad_clock_bound_is_not_accepted_after_three_attempts(monkeypatch, clock_helper):
    monkeypatch.setattr(clock_helper.time, "time", lambda: 102)
    commands = []

    def response(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps({"guest_after_epoch": 100}))

    monkeypatch.setattr(clock_helper.subprocess, "run", response)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        clock_helper.sync_site(2, [], "test-code", apply=True)
    assert len(commands) == 3


def test_identity_or_ssh_failure_is_not_retried(monkeypatch, clock_helper):
    calls = []

    def failed(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(clock_helper.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        clock_helper.sync_site(1, [], "test-code", apply=True)
    assert len(calls) == 1


def test_readonly_probe_never_sets_clock_or_retries(monkeypatch, clock_helper):
    monkeypatch.setattr(clock_helper.time, "time", lambda: 102)
    calls = []

    def response(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({"guest_after_epoch": 100}))

    monkeypatch.setattr(clock_helper.subprocess, "run", response)
    result = clock_helper.sync_site(1, [], "test-code")
    assert result["after_offset_upper_bound_seconds"] == 2
    assert len(calls) == 1
    assert "--apply" not in calls[0]

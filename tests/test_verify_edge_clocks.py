"""Read-only clock evidence must reject identity faults and clock/transport skew."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def clocks():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify_edge_clocks.py'
    spec = importlib.util.spec_from_file_location('clock_verification_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_remote(clocks, monkeypatch, hostname='sdw-edge-a', guest='100.1'):
    calls = []

    def remote(options, target, command):
        calls.append(command)
        if 'hostname' in command:
            return hostname
        if 'address' in command:
            return json.dumps([{'ifname': 'eth0', 'address': '52:54:00:53:01:00',
                                'addr_info': [{'local': '192.168.56.11'}]}])
        return guest

    monkeypatch.setattr(clocks, 'remote', remote)
    return calls


def test_read_only_native_clock_has_both_bounds_inside_one_second(clocks, monkeypatch):
    calls = install_remote(clocks, monkeypatch)
    ticks = iter([100.0, 100.2])
    monkeypatch.setattr(clocks.time, 'time', lambda: next(ticks))
    result = clocks.observe(1, [])
    assert result['changed'] is False
    assert result['clock_offset_bounds_seconds'] == pytest.approx([-.1, .1])
    assert not any('set' in command for command in calls)


@pytest.mark.parametrize('guest', ['95', '105'])
def test_skewed_clock_cannot_be_accepted_by_retrying_transport(clocks, monkeypatch, guest):
    install_remote(clocks, monkeypatch, guest=guest)
    ticks = iter([100.0, 100.2] * 3)
    monkeypatch.setattr(clocks.time, 'time', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='exceeds one second'):
        clocks.observe(1, [])


def test_wrong_guest_identity_is_rejected_before_reading_time(clocks, monkeypatch):
    calls = install_remote(clocks, monkeypatch, hostname='another-machine')
    with pytest.raises(ValueError, match='identity'):
        clocks.observe(1, [])
    assert not any('date' in command for command in calls)

"""Guard fixtures own no namespaces and signal no real processes."""

import json
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from lab import workload_guard as guard


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root, proc, netns = tmp_path / "root", tmp_path / "proc", tmp_path / "netns"
    for directory in (root, proc, netns):
        directory.mkdir()
    (root / ".ai-sdwan-owned").touch()
    (root / "lab/runtime").mkdir(parents=True)
    (proc / "self/ns").mkdir(parents=True)
    (proc / "self/ns/net").touch()
    os.link(proc / "self/ns/net", netns / "sdw-server")
    parent = proc / "1234"
    parent.mkdir()
    (parent / "cmdline").write_bytes(b"python\0-m\0lab.real_traffic\0start\0")
    (parent / "stat").write_text("1234 (python with spaces) S " + "0 " * 18 + "42")
    state = root / "lab/runtime/real-traffic.json"
    state.write_text(
        json.dumps({"kind": "real_traffic", "pid": 1234, "active": True, "updated_at": 1000})
    )
    monkeypatch.setattr(guard, "ROOT", root)
    monkeypatch.setattr(guard, "PROC_ROOT", proc)
    monkeypatch.setattr(guard, "NETNS_ROOT", netns)
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="posix", geteuid=lambda: 0))
    monkeypatch.setattr(guard.time, "time", lambda: 1001)
    return SimpleNamespace(root=root, proc=proc, netns=netns, state=state)


def test_commands_accept_only_scoped_servers():
    for index in (1, 2, 3):
        for side in ("a", "b"):
            namespace, command = guard.scoped_command("receiver", f"wan{index}", side)
            assert namespace == f"sdw-load{index}{side}"
            assert command[:3] == ["iperf3", "-s", "-B"]
            assert command[3] == f"172.16.{index}.{101 if side == 'a' else 102}"
    for kind, module in (("http", "lab.application_traffic"), ("udp", "lab.traffic")):
        namespace, command = guard.scoped_command(kind)
        assert namespace == "sdw-server" and command[2] == module
    for arguments in (
        ("shell",),
        ("receiver", "wan4", "a"),
        ("receiver", "wan1", "c"),
        ("http", "wan1", "a"),
    ):
        with pytest.raises(ValueError):
            guard.scoped_command(*arguments)


def test_scope_requires_root_marker_and_exact_namespace(owned, monkeypatch):
    guard.require_scope("sdw-server")
    (owned.netns / "different").touch()
    with pytest.raises(RuntimeError, match="exact owned namespace"):
        guard.require_scope("different")
    (owned.root / ".ai-sdwan-owned").unlink()
    with pytest.raises(RuntimeError, match="owned Linux lab"):
        guard.require_scope("sdw-server")
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="posix", geteuid=lambda: 1000))
    with pytest.raises(RuntimeError, match="requires root"):
        guard.require_scope("sdw-server")


def test_parent_identity_checks_tokens_start_ticks_and_pid_reuse(owned):
    assert guard.parent_identity(1234) == 42
    assert guard.parent_is_active(1234, 42)
    assert not guard.parent_is_active(1234, 41)
    cmdline = owned.proc / "1234/cmdline"
    cmdline.write_bytes(b"python\0some_lab.real_traffic_wrapper\0start\0")
    assert not guard.parent_is_active(1234, 42)
    cmdline.unlink()
    assert not guard.parent_is_active(1234, 42)


@pytest.mark.parametrize(
    "updates,now,expected",
    [
        ({}, 1025, True),
        ({}, 1026, False),
        ({"starting": True, "active": False}, 1060, True),
        ({"starting": True, "active": False}, 1061, False),
        ({"active": False}, 1001, False),
        ({"pid": 9999}, 1001, False),
        ({"pid": True}, 1001, False),
        ({"updated_at": float("nan")}, 1001, False),
        ({"updated_at": 1002}, 1001, False),
        ({"kind": "other"}, 1001, False),
    ],
)
def test_heartbeat_is_fresh_matching_and_active(owned, updates, now, expected):
    document = json.loads(owned.state.read_text())
    document.update(updates)
    owned.state.write_text(json.dumps(document))
    assert guard.parent_is_active(1234, 42, now) is expected


class Child:
    def __init__(self, *, stubborn=False):
        self.code = None
        self.calls = []
        self.stubborn = stubborn

    def poll(self):
        return self.code

    def terminate(self):
        self.calls.append("terminate")
        if not self.stubborn:
            self.code = -15

    def kill(self):
        self.calls.append("kill")
        self.code = -9

    def wait(self, timeout):
        self.calls.append(("wait", timeout))
        if self.code is None:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return self.code


def test_stopping_escalates_and_reaps_only_the_created_child():
    child = Child(stubborn=True)
    guard.stop_child(child)
    assert child.calls == ["terminate", ("wait", 5), "kill", ("wait", 5)]
    exited = Child()
    exited.code = 0
    guard.stop_child(exited)
    assert exited.calls == [("wait", 5)]


def test_parent_loss_stops_owned_server_and_wrong_scope_never_spawns(owned, monkeypatch):
    child, commands = Child(), []

    def spawn(command, **kwargs):
        commands.append((command, kwargs))
        owned.state.unlink()
        return child

    monkeypatch.setattr(guard.subprocess, "Popen", spawn)
    assert guard.run(1234, "http") == 1
    assert len(commands) == 1 and commands[0][0][2] == "lab.application_traffic"
    assert child.calls == ["terminate", ("wait", 5)]
    (owned.netns / "sdw-load1a").touch()
    with pytest.raises(RuntimeError, match="exact owned namespace"):
        guard.run(1234, "receiver", "wan1", "a")
    assert len(commands) == 1


def test_explicit_stop_reaps_child_without_signaling_parent(owned, monkeypatch):
    child = Child()
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_args, **_kwargs: child)
    stopped = threading.Event()
    stopped.set()
    assert guard.run(1234, "udp", stopped=stopped) == 0
    assert child.calls == ["terminate", ("wait", 5)]

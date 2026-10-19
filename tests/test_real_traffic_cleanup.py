"""Owned-process cleanup must preserve guard grace and continue on failure."""

import signal
import subprocess

from lab.real_traffic import stop_processes


class OwnedProcess:
    def __init__(self, pid, *, required_grace=0, signal_error=None, wait_errors=(), kill_error=None):
        self.pid = pid
        self.required_grace = required_grace
        self.signal_error = signal_error
        self.wait_errors = list(wait_errors)
        self.kill_error = kill_error
        self.signals = []
        self.timeouts = []
        self.kills = 0
        self.exit = None

    def poll(self):
        return self.exit

    def send_signal(self, value):
        self.signals.append(value)
        if self.signal_error:
            raise self.signal_error

    def wait(self, timeout):
        self.timeouts.append(timeout)
        if self.wait_errors:
            error = self.wait_errors.pop(0)
            if error:
                raise error
        if timeout < self.required_grace and self.kills == 0:
            raise subprocess.TimeoutExpired("owned guard", timeout)
        self.exit = 0 if self.kills == 0 else -9
        return self.exit

    def kill(self):
        self.kills += 1
        if self.kill_error:
            raise self.kill_error


def test_guard_gets_time_to_reap_its_child_before_outer_kill():
    # Inner cleanup can consume 5 s to terminate plus another 5 s to reap.
    guard = OwnedProcess(10, required_grace=10)
    worker = OwnedProcess(11)
    errors = stop_processes([guard, worker], [], [guard])
    assert errors == []
    assert guard.signals == [signal.SIGTERM]
    assert guard.timeouts == [12] and guard.kills == 0
    assert worker.timeouts == [5] and worker.kills == 0


def test_capture_receives_interrupt_to_flush_pcap_while_workers_receive_terminate():
    capture, worker = OwnedProcess(20), OwnedProcess(21)
    assert stop_processes([capture, worker], [capture], []) == []
    assert capture.signals == [signal.SIGINT]
    assert worker.signals == [signal.SIGTERM]
    assert capture.timeouts == worker.timeouts == [5]


def test_stuck_owned_process_is_killed_and_reaped_after_grace_expires():
    process = OwnedProcess(30, wait_errors=[subprocess.TimeoutExpired("owned worker", 5)])
    assert stop_processes([process], [], []) == []
    assert process.signals == [signal.SIGTERM]
    assert process.timeouts == [5, 5] and process.kills == 1
    assert process.exit == -9


def test_signal_and_wait_failure_of_one_process_does_not_skip_other_cleanup():
    failed = OwnedProcess(40, signal_error=PermissionError("fixture"),
                          wait_errors=[OSError("fixture")])
    survivor = OwnedProcess(41)
    errors = stop_processes([failed, survivor], [], [])
    assert len(errors) == 2
    assert any("40" in error and "signal" in error for error in errors)
    assert any("40" in error and "cleanup" in error for error in errors)
    assert survivor.signals == [signal.SIGTERM] and survivor.timeouts == [5]
    assert survivor.exit == 0


def test_failed_escalation_is_reported_and_other_owned_process_is_still_reaped():
    failed = OwnedProcess(50, wait_errors=[subprocess.TimeoutExpired("owned worker", 5)],
                          kill_error=PermissionError("fixture"))
    survivor = OwnedProcess(51)
    errors = stop_processes([failed, survivor], [], [])
    assert len(errors) == 1 and "50" in errors[0] and "PermissionError" in errors[0]
    assert failed.kills == 1
    assert survivor.exit == 0 and survivor.timeouts == [5]


def test_process_exiting_during_signal_is_reaped_without_false_failure():
    exited = OwnedProcess(60, signal_error=ProcessLookupError("fixture"))
    survivor = OwnedProcess(61)
    assert stop_processes([exited, survivor], [], []) == []
    assert exited.timeouts == survivor.timeouts == [5]


def test_already_exited_process_is_reaped_without_sending_another_signal():
    exited = OwnedProcess(70)
    exited.exit = 0
    assert stop_processes([exited], [], []) == []
    assert exited.signals == [] and exited.timeouts == [5] and exited.kills == 0

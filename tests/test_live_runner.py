"""Failure-handling tests with explicit fixtures; never run a network/process lab."""

import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab import run_live_eval as runner


@pytest.fixture(autouse=True)
def forbid_real_lab_activity(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This test must not contact a controller or change the host")

    for name in ("FlexiWANAdapter", "child", "terminate", "apply", "edge_fault", "require_linux"):
        monkeypatch.setattr(runner, name, forbidden)
    monkeypatch.setattr(runner.httpx, "Client", forbidden)
    monkeypatch.setattr(runner.socket, "socket", forbidden)


def packet_fixture(directory, *, capture=True):
    """An artifact fixture for evidence classification, not actual packet observations."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "packets.json").write_text(
        json.dumps(
            {
                "target": "10.30.0.2",
                "packet_bytes": 1250,
                "intervals": [
                    {
                        "second": 0,
                        "sent_packets": 200,
                        "received_packets": 198,
                        "measurement": "udp_echo_rtt_stddev",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # tcpdump classic-PCAP header; a fixture wire packet exists only if capture=True.
    header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 160, 1)
    record = struct.pack("<IIII", 0, 0, 64, 1300) + bytes(64) if capture else b""
    (directory / "wan1.pcap").write_bytes(header + record)


def test_existing_trial_directory_is_rejected_before_controller_access(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    destination.mkdir()
    artifact = destination / "live.sqlite3"
    artifact.write_bytes(b"previous trial must survive")
    accessed = []
    monkeypatch.setattr(runner, "FlexiWANAdapter", lambda _: accessed.append(True))

    with pytest.raises(FileExistsError):
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    assert not accessed
    assert artifact.read_bytes() == b"previous trial must survive"


def test_failed_preflight_has_fresh_artifacts_and_redacted_error(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    closed = []
    failure = OSError("controller-secret-must-not-be-copied")

    def preflight():
        assert destination.is_dir()
        assert (destination / "trial.json").is_file()
        raise failure

    adapter = SimpleNamespace(
        client=SimpleNamespace(preflight=preflight, close=lambda: closed.append(True))
    )
    monkeypatch.setattr(runner, "FlexiWANAdapter", lambda _: adapter)

    with pytest.raises(OSError) as captured:
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    assert captured.value is failure
    assert closed == [True]
    assert json.loads((destination / "states.json").read_text()) == []
    detail = json.loads((destination / "trial.json").read_text())
    assert detail["status"] == "failed"
    assert detail["phase"] == "controller_preflight"
    assert detail["error_type"] == "OSError"
    assert detail["evidence_level"] == "live_lab_attempt"
    assert not detail["packet_measurement_started"]
    assert not detail["packet_measurement_completed"]
    assert "controller-secret" not in (destination / "trial.json").read_text()


def virtual_lab(monkeypatch, tmp_path, destination, *, fail_capture=False, configure_error=None):
    """Protocol/process fixtures exercise runner lifecycle without starting anything."""
    journal = tmp_path / "intent.json"
    journal.write_text("{}", encoding="utf-8")
    scope = []

    def install_policy(body, identifier):
        assert destination.is_dir()
        scope.append(identifier)

    adapter = SimpleNamespace(
        client=SimpleNamespace(
            preflight=lambda: ({"rules": []}, []),
            readback=lambda policy: None,
            close=lambda: None,
        ),
        journal=SimpleNamespace(file=lambda identifier: journal),
        apply_experiment_policy=install_policy,
    )
    monkeypatch.setattr(runner, "FlexiWANAdapter", lambda _: adapter)
    monkeypatch.setattr(runner, "ensure_application", lambda client, profile: "fixture-app")
    monkeypatch.setattr(runner, "experiment_policy", lambda *args: {"rules": []})
    monkeypatch.setattr(runner, "apply", lambda plan: None)
    monkeypatch.setattr(runner, "edge_fault", lambda key, mode: None)
    monkeypatch.setattr(runner, "copy_audit", lambda *args: None)
    monkeypatch.setattr(
        runner,
        "verify_elapsed_clocks",
        lambda wall, start, previous: runner.time.monotonic() - start,
    )
    monkeypatch.setattr(runner, "terminate", lambda process: None)

    class Process:
        returncode = 0

        def __init__(self, capture):
            self.capture = capture

        def poll(self):
            return 0 if self.capture and fail_capture else None

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(runner, "child", lambda arguments, log: Process("tcpdump" in arguments))

    class Reply:
        status_code = 200

        def __init__(self, value):
            self.value = value

        def json(self):
            return self.value

        def raise_for_status(self):
            return self

    class HTTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path):
            if path == "/health":
                return Reply({"backend": "flexiwan", "model_loaded": False})
            return Reply({"telemetry": {"wan1": {"up": True}}, "last_decision": None})

        def post(self, path, json):
            if configure_error is not None:
                raise configure_error
            return Reply({})

    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: HTTP())
    clock = iter(range(10000))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    return scope


def test_measurement_failure_keeps_state_checkpoint_and_failure_phase(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    scope = virtual_lab(monkeypatch, tmp_path, destination, fail_capture=True)

    with pytest.raises(RuntimeError, match="PCAP capture failed"):
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    states = json.loads((destination / "states.json").read_text())
    detail = json.loads((destination / "trial.json").read_text())
    assert len(scope) == 1
    assert len(states) == detail["state_observations"] == 1
    assert states[0]["state"]["telemetry"]["wan1"]["up"]
    assert detail["phase"] == "packet_measurement"
    assert detail["packet_measurement_started"]
    assert not detail["packet_measurement_completed"]
    assert detail["evidence_level"] == "live_lab_attempt"


def test_failed_controller_apply_keeps_unresolved_baseline_journal(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    virtual_lab(monkeypatch, tmp_path, destination)
    failure = RuntimeError("fixture unknown controller outcome")
    adapter_factory = runner.FlexiWANAdapter

    def adapter_with_failed_apply(config):
        adapter = adapter_factory(config)

        def failed_apply(body, identifier):
            adapter.journal.file(identifier).write_text(
                json.dumps({"phase": "intent", "request_id": identifier}), encoding="utf-8"
            )
            raise failure

        adapter.apply_experiment_policy = failed_apply
        return adapter

    monkeypatch.setattr(runner, "FlexiWANAdapter", adapter_with_failed_apply)

    with pytest.raises(RuntimeError) as captured:
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    assert captured.value is failure
    detail = json.loads((destination / "trial.json").read_text())
    intent = json.loads((destination / "baseline-policy.json").read_text())
    assert detail["phase"] == "controller_apply"
    assert detail["baseline_journal_id"] == intent["request_id"]
    assert intent["phase"] == "intent"
    assert detail["evidence_level"] == "live_lab_attempt"


def test_cleanup_errors_do_not_replace_primary_failure(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    primary = FileNotFoundError("fixture service configuration failure")
    virtual_lab(monkeypatch, tmp_path, destination, configure_error=primary)

    def failed_termination(process):
        if process is not None:
            raise OSError("fixture cleanup failure")

    monkeypatch.setattr(runner, "terminate", failed_termination)

    with pytest.raises(FileNotFoundError) as captured:
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    assert captured.value is primary
    detail = json.loads((destination / "trial.json").read_text())
    assert detail["error_type"] == "FileNotFoundError"
    assert detail["phase"] == "service_configuration"
    assert detail["cleanup_errors"] == [{"action": "terminate_child", "error_type": "OSError"}]


def test_persistence_failure_does_not_replace_primary_failure(tmp_path, monkeypatch):
    destination = tmp_path / "trial"
    failure = KeyError("fixture preflight failure")
    adapter = SimpleNamespace(
        client=SimpleNamespace(preflight=lambda: (_ for _ in ()).throw(failure), close=lambda: None)
    )
    monkeypatch.setattr(runner, "FlexiWANAdapter", lambda _: adapter)
    original_write = runner.write_json

    def failed_states(path, value):
        if path.name == "states.json":
            raise OSError("fixture disk error")
        original_write(path, value)

    monkeypatch.setattr(runner, "write_json", failed_states)

    with pytest.raises(KeyError) as captured:
        runner.trial(None, Path("config.json"), "key", "S01", "voice", "reactive", destination)

    assert captured.value is failure
    assert captured.value.__notes__ == ["Could not save states.json: OSError"]
    assert json.loads((destination / "trial.json").read_text())["error_type"] == "KeyError"


def test_artifact_classification_requires_udp_observations_and_a_pcap_packet(tmp_path):
    assert runner.packet_evidence(tmp_path)["evidence_level"] == "live_lab_attempt"
    packet_fixture(tmp_path, capture=False)
    assert runner.packet_evidence(tmp_path)["evidence_level"] == "live_lab_attempt"
    packet_fixture(tmp_path)
    result = runner.packet_evidence(tmp_path)
    assert result["evidence_level"] == "real_packets"
    assert result["packet_artifacts"] == ["packets.json", "wan1.pcap"]
    (tmp_path / "packets.json").write_text("{truncated", encoding="utf-8")
    assert runner.packet_evidence(tmp_path)["evidence_level"] == "live_lab_attempt"


def prepare_main(monkeypatch, tmp_path, output):
    root = tmp_path / "repository"
    hosts = root / "lab/runtime/known_hosts"
    hosts.parent.mkdir(parents=True)
    hosts.write_text("fixture only; no SSH connection", encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "require_linux", lambda: None)
    monkeypatch.setattr(runner, "assert_shaping_available", lambda: None)
    monkeypatch.setattr(
        runner,
        "FlexiWANConfig",
        SimpleNamespace(load=lambda path: SimpleNamespace(model_dump=dict)),
    )

    class UnusedPort:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect_ex(self, address):
            return 111  # A fixture response; no socket was constructed.

    monkeypatch.setattr(runner.socket, "socket", UnusedPort)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "live-eval",
            "--config",
            "fixture.json",
            "--ssh-key",
            "fixture-key",
            "--scenarios",
            "S01",
            "S02",
            "--profiles",
            "voice",
            "--strategies",
            "reactive",
            "--repetitions",
            "1",
            "--output",
            str(output),
        ],
    )


@pytest.mark.parametrize("busy_port", [8000, 8001])
def test_existing_dashboard_or_runner_prevents_a_second_experiment(
    monkeypatch, tmp_path, busy_port
):
    destination = tmp_path / "new-experiment"
    prepare_main(monkeypatch, tmp_path, destination)

    class BusyPort:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect_ex(self, address):
            return 0 if address[1] == busy_port else 111

    monkeypatch.setattr(runner.socket, "socket", BusyPort)
    with pytest.raises(RuntimeError, match=f"Port {busy_port} is in use"):
        runner.main()
    assert not destination.exists()


@pytest.mark.parametrize("failure_type", [FileNotFoundError, ValueError, KeyError])
def test_unexpected_trial_errors_are_recorded_and_stop_the_experiment(
    tmp_path, monkeypatch, failure_type
):
    output = tmp_path / "output"
    prepare_main(monkeypatch, tmp_path, output)
    calls = []
    failure = failure_type("fixture failure; no external activity")

    def failed_trial(*args):
        calls.append(args[-1])
        raise failure

    monkeypatch.setattr(runner, "trial", failed_trial)

    with pytest.raises(failure_type) as captured:
        runner.main()

    assert captured.value is failure
    assert len(calls) == 1
    results = json.loads((output / "results.json").read_text())
    assert not results["complete"]
    assert results["evidence_level"] == "live_lab_attempt"
    assert results["packet_observed_trials"] == 0
    assert not results["native_qbr_evaluated"]
    assert results["trials"][0]["error_type"] == failure_type.__name__
    assert not results["trials"][0]["valid"]
    assert not results["trials"][0]["packet_measurement_completed"]


def test_existing_output_without_results_is_preserved(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    old = output / "partially-completed.sqlite3"
    old.write_bytes(b"preserve interrupted experiment")
    prepare_main(monkeypatch, tmp_path, output)
    monkeypatch.setattr(runner, "trial", lambda *args: pytest.fail("Must not reuse old output"))

    with pytest.raises(RuntimeError, match="Existing live output"):
        runner.main()

    assert old.read_bytes() == b"preserve interrupted experiment"
    assert not (output / "experiment.json").exists()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_experiment_boundary_does_not_catch_base_exception(tmp_path, monkeypatch, interruption):
    output = tmp_path / "output"
    prepare_main(monkeypatch, tmp_path, output)

    def interrupted_trial(*args):
        raise interruption()

    monkeypatch.setattr(runner, "trial", interrupted_trial)

    with pytest.raises(interruption):
        runner.main()

    assert not (output / "results.json").exists()


def test_mixed_experiment_evidence_keeps_setup_failure_visible(tmp_path):
    rows = [
        {
            "strategy": "reactive",
            "evidence_level": "real_packets",
            "packet_measurement_completed": True,
            "valid": True,
        },
        {
            "strategy": "native_qbr",
            "evidence_level": "live_lab_attempt",
            "packet_measurement_completed": False,
            "valid": False,
        },
    ]
    runner.save_results(tmp_path, rows, False, 91001)
    results = json.loads((tmp_path / "results.json").read_text())
    assert results["evidence_level"] == "real_packets"
    assert results["packet_observed_trials"] == 1
    assert not results["native_qbr_evaluated"]
    assert not results["complete"]
    assert len(results["trials"]) == 2

"""Profile and lifecycle fixtures; no host qdisc, socket or controller changes."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from lab import impairments, natural_wan


def frames(seed):
    model = natural_wan.NaturalModel(seed)
    return [model.step(second) for second in range(0, 1800, 10)]


def test_seeded_profiles_are_bounded_independent_and_time_varying():
    first = frames(20261009)
    assert first == frames(20261009)
    assert first != frames(42)
    for link, capacity in impairments.CAPACITY.items():
        assert len({row["wans"][link]["delay_ms"] for row in first}) > 50
        for row in first:
            config = row["wans"][link]
            assert 5 <= config["delay_ms"] <= 90
            assert 0 < config["jitter_ms"] <= config["delay_ms"] * 0.401
            assert config["rate_mbps"] == capacity
            assert 0 < row["load_mbps"][link] < capacity * 0.5
        assert any(row["regimes"][link] != "calm" for row in first)
    assert (
        first[0]["wans"]["wan1"]["delay_ms"]
        < first[0]["wans"]["wan2"]["delay_ms"]
        < first[0]["wans"]["wan3"]["delay_ms"]
    )
    assert (
        max(row["wans"]["wan3"]["delay_ms"] for row in first)
        > max(row["wans"]["wan1"]["delay_ms"] for row in first) * 2
    )


def test_natural_profile_time_cannot_go_backwards_or_repeat():
    model = natural_wan.NaturalModel()
    model.step(10)
    for value in (10, 5, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            model.step(value)


def test_commands_apply_burst_loss_to_only_six_owned_wan_interfaces(monkeypatch):
    commands = []
    monkeypatch.setattr(impairments, "run", lambda *arguments: commands.append(arguments))
    config = natural_wan.NaturalModel().step(0)
    impairments._apply(config, change=True)
    assert len(commands) == 6
    assert {command[3] for command in commands} == {"sdw-wan1", "sdw-wan2", "sdw-wan3"}
    assert {command[8] for command in commands} == {"a", "b"}
    assert all(command[6] == "change" and "gemodel" in command for command in commands)
    commands.clear()
    impairments._apply(impairments.plan("CONTROL"), change=True)
    assert all("gemodel" not in command and "0%" in command for command in commands)


def test_worker_failure_restores_baseline_and_records_stop(tmp_path, monkeypatch):
    updates, records, documents = [], [], []

    @contextmanager
    def lease():
        yield lambda config, **kwargs: updates.append(config)

    class DeadWorker:
        def poll(self):
            return 1

        def wait(self, **kwargs):
            return 1

    store = SimpleNamespace(
        stream="fixture",
        put=lambda key, value: records.append((key, value)),
        log=lambda now, kind, value: records.append((kind, value)),
    )
    monkeypatch.setattr(natural_wan, "ROOT", tmp_path)
    monkeypatch.setattr(natural_wan, "require_owned", lambda: None)
    monkeypatch.setattr(natural_wan, "inspect_baseline", lambda: {"fixture": True})
    monkeypatch.setattr(natural_wan, "live_store", lambda stream: store)
    monkeypatch.setattr(natural_wan, "shaping_session", lease)
    monkeypatch.setattr(natural_wan, "atomic_state", lambda doc: documents.append(dict(doc)))
    monkeypatch.setattr(natural_wan.signal, "signal", lambda *args: None)
    monkeypatch.setattr(natural_wan.subprocess, "Popen", lambda *args, **kwargs: DeadWorker())
    args = SimpleNamespace(seed=1, stream="fixture", interval=10, duration=0)
    with pytest.raises(RuntimeError, match="worker stopped"):
        natural_wan.run_environment(args)
    assert updates[-1] == impairments.plan("CONTROL")
    assert documents[-1]["active"] is False and documents[-1]["restored_baseline"]
    assert records[-1][0] == "natural_wan_stopped"


def test_live_runner_refuses_busy_shaper_before_controller_access(tmp_path, monkeypatch):
    from lab import run_live_eval as runner
    from tests.test_live_runner import prepare_main

    prepare_main(monkeypatch, tmp_path, tmp_path / "new-output")

    def busy():
        raise RuntimeError("WAN shaping is in use")

    monkeypatch.setattr(runner, "assert_shaping_available", busy)
    monkeypatch.setattr(
        runner, "FlexiWANAdapter", lambda *args: pytest.fail("No controller mutation")
    )
    with pytest.raises(RuntimeError, match="shaping is in use"):
        runner.main()


@pytest.mark.parametrize("missing_link", ["all", "wan1", "wan2", "wan3"])
def test_start_requires_measured_telemetry_for_each_wan(tmp_path, monkeypatch, missing_link):
    import ssl

    import httpx

    from sdwan.storage import Store

    private = tmp_path / "lab/runtime/observability/private.json"
    private.parent.mkdir(parents=True)
    private.write_text(json.dumps({"database_url": "fixture", "api_token": "fixture"}))
    telemetry = {link: {"up": True, "fresh": True} for link in impairments.CAPACITY}
    if missing_link == "all":
        telemetry.clear()
    else:
        del telemetry[missing_link]
    response = SimpleNamespace(json=lambda: {"backend": "flexiwan", "telemetry": telemetry})
    response.raise_for_status = lambda: response

    @contextmanager
    def connection():
        yield SimpleNamespace(
            execute=lambda *args: SimpleNamespace(fetchone=lambda: {"stream": "fixture"})
        )

    @contextmanager
    def client(**kwargs):
        yield SimpleNamespace(get=lambda *args, **kwargs: response)

    monkeypatch.setattr(natural_wan, "ROOT", tmp_path)
    monkeypatch.setattr(Store, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(Store, "connection", lambda self: connection())
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: None)
    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(RuntimeError, match="Three fresh measured WANs"):
        natural_wan.live_store(None)

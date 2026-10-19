"""Forecast reuse preserves telemetry, sampling and reactive fallback contracts."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import sdwan.service as service_module
from sdwan.domain import LINKS, WINDOW
from sdwan.features import regular_history
from sdwan.service import Service


class CountingModel:
    def __init__(self, offset=0):
        self.calls = 0
        self.offset = offset

    def predict(self, history):
        self.calls += 1
        if not regular_history(history):
            return None
        values = history[-1].vector()
        values[0] += self.offset
        return values


def rows(timestamp):
    return [
        {
            "link_id": link,
            "timestamp": timestamp,
            "up": True,
            "latency_ms": 20 + index,
            "jitter_ms": 3,
            "loss_pct": 0,
            "utilization_pct": 15,
        }
        for index, link in enumerate(LINKS)
    ]


@pytest.fixture
def service(tmp_path, monkeypatch):
    model = CountingModel()
    monkeypatch.setattr(service_module, "Forecaster", lambda _: model)
    return Service(tmp_path / "cache.sqlite3", tmp_path / "model")


def populate(service, length=WINDOW):
    for index in range(length):
        timestamp = 100 + index * 5
        assert service.ingest(rows(timestamp), timestamp)["accepted"] == len(LINKS)
    return timestamp


def test_same_frame_reuses_forecasts_across_reads_and_watchdog_ticks(service):
    now = populate(service)
    expected = {link: service.latest[link].vector() for link in LINKS}
    assert service.forecasts() == expected
    for offset in range(20):
        assert service.forecasts() == expected
        service.status(now + offset)
        service.tick(now + offset)
    assert service.model.calls == len(LINKS)
    assert service.tick(now + 20)["last_decision"]["action"] == "no_path"


def test_only_accepted_telemetry_invalidates_cached_forecasts(service):
    now = populate(service)
    expected = service.forecasts()
    assert service.ingest(rows(now), now)["accepted"] == 0  # Replayed frame.
    invalid = rows(now + 5)
    for row in invalid:
        row["loss_pct"] = 101
    assert service.ingest(invalid, now + 5)["accepted"] == 0
    assert service.forecasts() == expected
    assert service.model.calls == len(LINKS)
    updated = rows(now + 5)
    updated[0]["latency_ms"] = 48
    assert service.ingest(updated, now + 5)["accepted"] == len(LINKS)
    assert service.forecasts()["wan1"][0] == 48
    assert service.model.calls == 2 * len(LINKS)


def test_configuration_does_not_repeat_measurement_only_inference(service):
    now = populate(service)
    expected = service.forecasts()
    service.configure(mode="auto", profile="bulk", now=now)
    assert service.forecasts() == expected
    assert service.model.calls == len(LINKS)


def test_replacing_disabling_and_restoring_model_invalidates_cache(service):
    populate(service)
    original = service.model
    expected = service.forecasts()
    replacement = CountingModel(offset=7)
    service.model = replacement
    assert service.forecasts()["wan1"][0] == expected["wan1"][0] + 7
    assert replacement.calls == len(LINKS)
    service.model = None
    assert service.forecasts() == {}
    service.model = original
    assert service.forecasts() == expected
    assert original.calls == 2 * len(LINKS)


def test_sampling_contract_change_invalidates_cache(service):
    populate(service)
    expected = service.forecasts()
    service.forecast_sampling = "causal_linear_5s"
    assert service.forecasts() == expected
    assert service.model.calls == 2 * len(LINKS)
    assert service.forecasts() == expected
    assert service.model.calls == 2 * len(LINKS)


def test_empty_prediction_result_is_cached_until_measurement_arrives(service):
    now = populate(service, length=WINDOW - 1)
    for _ in range(20):
        assert service.forecasts() == {}
    assert service.model.calls == len(LINKS)
    service.ingest(rows(now + 5), now + 5)
    assert set(service.forecasts()) == set(LINKS)
    assert service.model.calls == 2 * len(LINKS)


def test_unavailable_causal_history_is_not_resampled_on_every_read(service, monkeypatch):
    service.forecast_sampling = "causal_linear_5s"
    now = populate(service, length=WINDOW - 1)
    sampling_calls = []
    resample = service_module.resample_live_history

    def counting_resample(history):
        sampling_calls.append(len(history))
        return resample(history)

    monkeypatch.setattr(service_module, "resample_live_history", counting_resample)
    for _ in range(20):
        assert service.forecasts() == {}
    assert sampling_calls == [WINDOW - 1] * len(LINKS)
    assert service.model.calls == 0
    service.ingest(rows(now + 5), now + 5)
    assert set(service.forecasts()) == set(LINKS)
    assert len(sampling_calls) == 2 * len(LINKS)
    assert service.model.calls == len(LINKS)


def test_callers_cannot_mutate_first_or_reused_forecast_values(service):
    populate(service)
    expected = {link: service.latest[link].vector() for link in LINKS}
    first = service.forecasts()
    first["wan1"][0] = 999
    first.pop("wan2")
    second = service.forecasts()
    assert second == expected
    second["wan3"].append(999)
    second.clear()
    assert service.forecasts() == expected
    assert service.model.calls == len(LINKS)


def test_prediction_failure_discards_partial_results_and_retries_same_frame(service):
    class TransientModel(CountingModel):
        def predict(self, history):
            values = super().predict(history)
            if self.calls == 2:
                raise RuntimeError("Transient inference failure")
            return values

    populate(service)
    service.model = TransientModel()
    assert service.forecasts() == {}
    error = "Prediction failed: RuntimeError; reactive fallback"
    assert service.model_error == error
    expected = {link: service.latest[link].vector() for link in LINKS}
    assert service.forecasts() == expected
    assert service.model.calls == 2 + len(LINKS)
    assert service.forecasts() == expected
    assert service.model.calls == 2 + len(LINKS)
    # Existing diagnostic semantics retain the failure until explicitly changed.
    assert service.model_error == error


def test_concurrent_forecast_readers_compute_one_frame_and_receive_independent_values(service):
    populate(service)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: service.forecasts(), range(32)))
    expected = {link: service.latest[link].vector() for link in LINKS}
    assert all(result == expected for result in results)
    assert service.model.calls == len(LINKS)
    assert len({id(result["wan1"]) for result in results}) == len(results)

"""Unit tests inject memory fixtures; production has only TimescaleDB storage."""

import copy
import json

import pytest


class MemoryStore:
    backend = "unit-test-memory"

    def __init__(self):
        self.samples = []
        self.logs = []
        self.state = {}

    def ingest(self, samples):
        self.samples.extend(copy.deepcopy(sample.model_dump()) for sample in samples)

    def history(self, link_id, limit=12):
        return copy.deepcopy([row for row in self.samples if row["link_id"] == link_id][-limit:])

    def log(self, now, kind, payload):
        json.dumps(payload, allow_nan=False)
        self.logs.append(
            {
                "id": len(self.logs) + 1,
                "timestamp": now,
                "kind": kind,
                "payload": copy.deepcopy(payload),
            }
        )

    def events(self, limit=100):
        return copy.deepcopy(list(reversed(self.logs[-limit:])))

    def get(self, key, default=None):
        return copy.deepcopy(self.state.get(key, default))

    def put(self, key, value):
        self.state[key] = copy.deepcopy(value)

    def export(self):
        return {
            "telemetry": copy.deepcopy(self.samples),
            "events": copy.deepcopy(self.logs),
            "state": copy.deepcopy(self.state),
        }


@pytest.fixture(autouse=True)
def unit_service_storage(monkeypatch):
    from sdwan import service

    stores = {}

    def factory(database_url=None, *, stream="live"):
        key = (str(database_url), stream)
        if key not in stores:
            stores[key] = MemoryStore()
        return stores[key]

    monkeypatch.setattr(service, "Store", factory)
    return factory

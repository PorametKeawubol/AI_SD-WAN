import threading
import time

import pytest
from fastapi.testclient import TestClient

from sdwan.adapters import ApplyResult, FlexiWANAdapter
from sdwan.api import create_app
from sdwan.domain import LINKS
from sdwan.service import Conflict, Service


def rows(now, failed="wan1"):
    return [
        {
            "link_id": link,
            "timestamp": now,
            "up": link != failed,
            "latency_ms": 25,
            "jitter_ms": 5,
            "loss_pct": 0.1,
            "utilization_pct": 30,
        }
        for link in LINKS
    ]


@pytest.fixture
def service(tmp_path):
    return Service(tmp_path / "lab.sqlite3", tmp_path / "missing-model")


def pending(service, now=100):
    service.ingest(rows(now), now)
    return service.tick(now)["pending"]["id"]


def test_manual_approve_and_replay_protection(service):
    identifier = pending(service)
    assert service.controller.current == "wan1"
    assert service.review(identifier, True, 101)["confirmed_path"] == "wan2"
    with pytest.raises(Conflict):
        service.review(identifier, True, 102)
    kinds = [row["kind"] for row in service.store.events()]
    assert "approved" in kinds and "applied" in kinds


def test_reject_keeps_route(service):
    identifier = pending(service)
    service.review(identifier, False, 101)
    assert service.controller.current == "wan1" and service.pending is None


def test_expired_approval_cannot_apply(service):
    identifier = pending(service)
    with pytest.raises(Conflict, match="expired"):
        service.review(identifier, True, 131)
    assert service.controller.current == "wan1"


def test_changed_network_invalidates_approval(service):
    identifier = pending(service)
    service.ingest(rows(105, failed="wan2"), 105)
    with pytest.raises(Conflict, match="network state"):
        service.review(identifier, True, 105)
    assert service.controller.current == "wan1"


def test_failed_apply_does_not_advance_confirmed_route(service):
    service.adapter = FlexiWANAdapter()
    identifier = pending(service)
    with pytest.raises(Conflict, match="apply failed"):
        service.review(identifier, True, 101)
    assert service.controller.current == "wan1"
    assert "apply_failed" in [event["kind"] for event in service.store.events()]


def test_unverified_apply_does_not_advance_confirmed_route(service):
    class Unverified:
        backend = "test"

        def apply(self, *args):
            return ApplyResult(False, "test", "submitted only")

    service.adapter = Unverified()
    identifier = pending(service)
    with pytest.raises(Conflict, match="apply failed"):
        service.review(identifier, True, 101)
    assert service.controller.current == "wan1"


def test_auto_apply_and_restart_persist_state(service, tmp_path):
    service.configure(mode="auto", now=99)
    pending_state = service.ingest(rows(100), 100)
    assert pending_state["accepted"] == 3
    assert service.tick(100)["confirmed_path"] == "wan2"
    restarted = Service(tmp_path / "lab.sqlite3", tmp_path / "missing-model")
    assert restarted.mode == "auto" and restarted.controller.current == "wan2"
    assert restarted.pending is None


def test_mode_change_invalidates_pending(service):
    identifier = pending(service)
    service.configure(mode="auto", now=101)
    with pytest.raises(Conflict):
        service.review(identifier, True, 101)
    assert service.controller.current == "wan1"


def test_ingest_quarantines_invalid_stale_and_replayed_rows(service):
    data = rows(100, failed=None)
    data[0]["loss_pct"] = 101
    data[1]["timestamp"] = 80
    result = service.ingest(data, 100)
    assert result["accepted"] == 1 and len(result["rejected"]) == 2
    assert service.ingest([data[2]], 101)["accepted"] == 0
    assert "wan1" not in service.latest


def test_stale_watchdog_alerts_when_telemetry_stops(service):
    service.ingest(rows(100, failed=None), 100)
    assert service.tick(100)["network_available"]
    result = service.tick(115)
    assert not result["network_available"] and result["last_decision"]["action"] == "no_path"


def test_api_auth_origin_and_manual_workflow(service):
    with TestClient(create_app(service, token="test-token")) as client:
        assert client.get("/api/state").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        assert (
            client.post(
                "/api/configuration",
                json={"mode": "auto"},
                headers={**headers, "Origin": "http://untrusted.example"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/configuration", json={"mode": "unknown"}, headers=headers).status_code
            == 422
        )
        response = client.post(
            "/api/telemetry", json={"samples": rows(time.time())}, headers=headers
        )
        assert response.status_code == 200 and response.json()["accepted"] == 3
        identifier = response.json()["state"]["pending"]["id"]
        response = client.post(
            f"/api/recommendations/{identifier}", json={"action": "approve"}, headers=headers
        )
        assert response.status_code == 200 and response.json()["confirmed_path"] == "wan2"
        assert (
            client.post(
                f"/api/recommendations/{identifier}", json={"action": "approve"}, headers=headers
            ).status_code
            == 409
        )
        redirect = client.get("/", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "https://localhost:3000/d/ai-sdwan"
        assert client.get("/app.js").status_code == 404
        schema = client.get("/openapi.json").json()
        assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
        docs = client.get("/docs")
        assert docs.status_code == 200
        assert "/openapi-assets/swagger-ui-bundle.js" in docs.text
        assert "nonce-" in docs.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


@pytest.mark.parametrize("fail", [False, True])
def test_live_apply_keeps_telemetry_responsive_until_verified(tmp_path, fail):
    release = threading.Event()
    started = threading.Event()

    class DelayedAdapter:
        backend = "flexiwan"

        def apply(self, source, target, recommendation_id):
            started.set()
            assert release.wait(5)
            if fail:
                raise RuntimeError("Simulated uncertain network response")
            return ApplyResult(True, self.backend, "Protocol fixture verified")

    live = Service(tmp_path / "live.sqlite3", tmp_path / "missing-model", adapter=DelayedAdapter())
    now = time.time()
    identifier = pending(live, now)
    try:
        state = live.review(identifier, True, now + 0.01)
        assert started.wait(1)
        assert state["confirmed_path"] == "wan1"
        assert state["apply_inflight"]["target"] == "wan2"
        assert live.ingest(rows(now + 0.1), now + 0.1)["accepted"] == 3
        assert live.tick(now + 0.1)["apply_inflight"] is not None
        assert "applied" not in [row["kind"] for row in live.store.events()]
        with pytest.raises(Conflict):
            live.review(identifier, True, now + 0.2)
    finally:
        release.set()
        live.close()
    assert live.controller.current == ("wan1" if fail else "wan2")
    assert ("apply_failed" if fail else "applied") in [row["kind"] for row in live.store.events()]


def test_live_backend_refuses_external_telemetry_injection(service):
    service.adapter.backend = "flexiwan"
    with TestClient(create_app(service)) as client:
        assert client.post("/api/telemetry", json={"samples": rows(time.time())}).status_code == 403


def test_native_qbr_observer_cannot_mutate_policy(tmp_path):
    from fastapi.testclient import TestClient

    from sdwan.api import create_app
    from sdwan.service import Service

    service = Service(tmp_path / "observe.sqlite3")
    service.observing = True
    service.adapter.backend = "flexiwan_observe"
    service.controller.current = None
    service.configure("manual")
    state = service.tick()
    assert state["pending"] is None and state["observing_native_qbr"]
    assert state["route_evidence"] == "agent_configuration"
    with TestClient(create_app(service)) as client:
        assert client.post("/api/configuration", json={"mode": "auto"}).status_code == 409
        assert client.post("/api/telemetry", json={"samples": [{}]}).status_code == 403

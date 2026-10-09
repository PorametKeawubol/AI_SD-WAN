import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from lab.edge.probe import counters, ping_metrics
from sdwan.adapters import FlexiWANAdapter
from sdwan.collector import FlexiWANTelemetryCollector
from sdwan.flexiwan import FlexiManageClient, FlexiWANConfig, IntegrationError

ORG, DEVICE, POLICY, RULE = (f"{number:024x}" for number in range(1, 5))
LABELS = {link: f"{number:024x}" for number, link in enumerate(("wan1", "wan2", "wan3"), 5)}


class ControllerFixture:
    """Protocol fixture only: simulated job states and agent replies, no data plane."""

    def __init__(self):
        self.policy = {
            "_id": POLICY,
            "name": "Lab",
            "description": "Dedicated",
            "applyOnWan": False,
            "overrideDefaultRoute": True,
            "rules": [
                {
                    "_id": RULE,
                    "name": "LAN",
                    "priority": 0,
                    "enabled": True,
                    "classification": {
                        "prefix": {"ip": "10.20.0.0/24", "ports": "", "protocol": ""}
                    },
                    "action": {
                        "order": "priority",
                        "fallback": "drop",
                        "links": [
                            {"order": "priority", "pathlabels": [{"_id": identifier}]}
                            for identifier in LABELS.values()
                        ],
                    },
                }
            ],
        }
        self.devices = [
            {
                "_id": DEVICE,
                "machineId": "test-machine",
                "isConnected": True,
                "isApproved": True,
                "deviceStatus": {"state": "running"},
                "policies": {"multilink": {"policy": POLICY, "status": "installed"}},
            }
        ]
        self.job_state = "complete"
        self.job_target = "test-machine"
        self.ids = [1]
        self.returned_job_ids = None
        self.fail_transport = False
        self.disconnect = False
        self.mismatch = False
        self.classifier_mismatch = False
        self.rule_flags_mismatch = False
        self.version = "6.3.37"
        self.writes = []
        self.requests = []
        self.probe = None

    def respond(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/spec":
            return httpx.Response(
                200, content=Path("contracts/fleximanage-6.3.37.openapi.yaml").read_bytes()
            )
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.params["org"] == ORG
        if path == "/api/version":
            payload = {"version": self.version}
        elif path == "/api/devices":
            offset = int(request.url.params.get("offset", 0))
            payload = self.devices[offset : offset + 100]
        elif path == "/api/pathlabels":
            payload = [{"_id": identifier} for identifier in LABELS.values()]
        elif path == f"/api/mlpolicies/{POLICY}":
            if request.method == "PUT":
                self.writes.append(json.loads(request.content))
                self.policy = self.writes[-1]
                if self.fail_transport:
                    raise httpx.ReadTimeout("Response lost after server persisted the change")
                payload = {**self.policy, "status": "completed", "ids": self.ids}
            else:
                payload = self.policy
        elif path == "/api/jobs":
            assert request.url.params["status"] == "all"
            assert request.url.params["ids"] == ",".join(map(str, self.ids))
            identifiers = self.ids if self.returned_job_ids is None else self.returned_job_ids
            payload = [
                {"_id": identifier, "state": self.job_state, "type": self.job_target}
                for identifier in identifiers
            ]
        elif path.startswith("/api/jobs/"):
            payload = {}  # Actual pinned upstream single-job endpoint returns an empty object.
        elif path.endswith("/configuration"):
            rule = copy.deepcopy(self.policy["rules"][0])
            rule["id"] = rule.pop("_id")
            rule["classification"].setdefault("application", {})
            rule["classification"].setdefault("prefix", {})
            if self.classifier_mismatch and self.writes:
                rule["classification"]["prefix"]["ip"] = "10.99.0.0/24"
            rule["apply-on-wan-rx"] = self.policy.get("applyOnWan", False)
            rule["override-default-route"] = self.policy.get("overrideDefaultRoute", False)
            if self.rule_flags_mismatch and self.writes:
                rule["override-default-route"] = not rule["override-default-route"]
            if self.mismatch and self.writes:
                rule["action"]["links"][0]["pathlabels"] = [{"_id": LABELS["wan3"]}]
            payload = {
                "error": None,
                "deviceStatus": "disconnected" if self.disconnect else "connected",
                "configuration": [
                    {"message": "add-multilink-policy", "params": {"id": POLICY, "rules": [rule]}}
                ],
            }
        elif path.endswith("/send"):
            payload = self.probe
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=payload)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXIWAN_ACCESS_KEY", "test-key")
    config = FlexiWANConfig.model_validate(
        {
            "base_url": "https://lab.invalid/api",
            "organization_id": ORG,
            "device_ids": [DEVICE],
            "policy_id": POLICY,
            "rule_id": RULE,
            "links": {
                link: {"path_label_id": identifier, "capacity_mbps": capacity}
                for (link, identifier), capacity in zip(LABELS.items(), (100, 80, 30))
            },
            "journal_dir": str(tmp_path / "journal"),
            "job_timeout_seconds": 0.01,
            "poll_seconds": 0.001,
        }
    )
    fixture = ControllerFixture()
    http = httpx.Client(
        base_url=config.base_url + "/", transport=httpx.MockTransport(fixture.respond)
    )
    client = FlexiManageClient(config, http)
    return fixture, FlexiWANAdapter(config, client)


def test_verified_apply_and_replay_rejection(lab):
    fixture, adapter = lab
    original_classification = copy.deepcopy(fixture.policy["rules"][0]["classification"])
    assert adapter.apply("wan1", "wan2", "r1").verified
    assert fixture.policy["rules"][0]["classification"] == original_classification
    assert fixture.policy["rules"][0]["_id"] == RULE
    with pytest.raises(IntegrationError, match="replay"):
        adapter.apply("wan2", "wan3", "r1")
    assert len(fixture.writes) == 1
    assert any(request.url.path == "/api/jobs" for request in fixture.requests)
    assert not any(request.url.path.startswith("/api/jobs/") for request in fixture.requests)


@pytest.mark.parametrize("returned_ids", [[], [2], [1, 1]])
def test_missing_substituted_or_duplicate_queue_records_freeze_further_writes(lab, returned_ids):
    fixture, adapter = lab
    fixture.returned_job_ids = returned_ids
    with pytest.raises(IntegrationError, match="expected jobs"):
        adapter.apply("wan1", "wan2", "r1")
    assert len(adapter.journal.unresolved()) == 1
    with pytest.raises(IntegrationError, match="Unresolved"):
        adapter.apply("wan1", "wan3", "r2")
    assert len(fixture.writes) == 1


def test_reconciliation_rejects_a_job_on_another_machine(lab):
    fixture, adapter = lab
    fixture.job_target = "another-machine"
    with pytest.raises(IntegrationError, match="Job target"):
        adapter.apply("wan1", "wan2", "r1")
    with pytest.raises(IntegrationError, match="Job target"):
        adapter.reconcile(rollback=True)
    assert len(fixture.writes) == 1 and adapter.journal.unresolved()


def test_native_qbr_and_priority_preserve_scope_and_share_classifier(lab):
    from lab.baselines import experiment_policy

    fixture, adapter = lab
    qbr = experiment_policy(adapter.client, fixture.policy, "voice", "native_qbr", f"{10:024x}")
    assert adapter.apply_experiment_policy(qbr, "qbr-1").verified
    assert adapter.client.current_path(fixture.policy) is None
    classification = copy.deepcopy(fixture.policy["rules"][0]["classification"])
    priority = experiment_policy(adapter.client, fixture.policy, "voice", "reactive", f"{10:024x}")
    assert adapter.apply_experiment_policy(priority, "priority-2").verified
    assert adapter.client.current_path(fixture.policy) == "wan1"
    assert fixture.policy["rules"][0]["classification"] == classification
    wrong_scope = copy.deepcopy(priority)
    wrong_scope["name"] = "Another administrator's policy"
    with pytest.raises(IntegrationError, match="unmanaged"):
        adapter.apply_experiment_policy(wrong_scope, "unsafe-3")
    assert len(fixture.writes) == 2


@pytest.mark.parametrize("attribute,value", [("version", "6.6.0"), ("disconnect", True)])
def test_preflight_failure_sends_no_write(lab, attribute, value):
    fixture, adapter = lab
    setattr(fixture, attribute, value)
    with pytest.raises(IntegrationError):
        adapter.apply("wan1", "wan2", "r1")
    assert not fixture.writes


def test_shared_policy_on_later_page_is_rejected(lab):
    fixture, adapter = lab
    fixture.devices.extend([{"_id": f"{n:024x}", "policies": {}} for n in range(100, 199)])
    other = copy.deepcopy(fixture.devices[0])
    other["_id"] = f"{999:024x}"
    fixture.devices.append(other)
    with pytest.raises(IntegrationError, match="scope"):
        adapter.apply("wan1", "wan2", "r1")
    assert not fixture.writes
    assert any(request.url.params.get("offset") == "100" for request in fixture.requests)


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("job_state", "active"),
        ("job_state", "failed"),
        ("job_target", "another-machine"),
        ("mismatch", True),
        ("classifier_mismatch", True),
        ("rule_flags_mismatch", True),
        ("ids", []),
    ],
)
def test_submission_is_not_ack_and_freezes_next_write(lab, attribute, value):
    fixture, adapter = lab
    setattr(fixture, attribute, value)
    with pytest.raises(IntegrationError):
        adapter.apply("wan1", "wan2", "r1")
    assert len(adapter.journal.unresolved()) == 1
    with pytest.raises(IntegrationError, match="Unresolved"):
        FlexiWANAdapter(adapter.config, adapter.client).apply("wan1", "wan3", "r2")
    assert len(fixture.writes) == 1


def test_lost_response_is_not_retried_and_can_readback_reconcile(lab):
    fixture, adapter = lab
    fixture.fail_transport = True
    with pytest.raises(IntegrationError):
        adapter.apply("wan1", "wan2", "r1")
    assert len(fixture.writes) == 1
    with pytest.raises(IntegrationError, match="Lost submission"):
        adapter.reconcile(rollback=True)
    assert adapter.reconcile() == "wan2"
    assert not adapter.journal.unresolved()


def test_failed_job_can_explicitly_restore_saved_policy(lab):
    fixture, adapter = lab
    original = copy.deepcopy(fixture.policy)
    fixture.job_state = "failed"
    with pytest.raises(IntegrationError):
        adapter.apply("wan1", "wan2", "r1")
    fixture.job_state = "complete"
    assert adapter.reconcile(rollback=True) == "wan1"
    assert fixture.policy == original
    assert not adapter.journal.unresolved()


def test_reconcile_refuses_concurrent_admin_change(lab):
    fixture, adapter = lab
    fixture.job_state = "failed"
    with pytest.raises(IntegrationError):
        adapter.apply("wan1", "wan2", "r1")
    fixture.policy["description"] = "Administrator changed this"
    with pytest.raises(IntegrationError, match="conflict"):
        adapter.reconcile(rollback=True)
    assert len(fixture.writes) == 1


def test_configuration_rejects_credentials_in_url(lab):
    _, adapter = lab
    config = adapter.config.model_dump()
    config["base_url"] = "https://secret:password@lab.invalid/api"
    with pytest.raises(ValidationError):
        FlexiWANConfig.model_validate(config)


def probe_reply(samples):
    return {
        "ok": 1,
        "message": {
            "returncode": 0,
            "output": json.dumps(
                {"schema": 1, "measurement": "icmp_rtt_stddev", "samples": samples}
            ),
        },
    }


def test_live_collector_rejects_missing_stale_corrupt(lab):
    fixture, adapter = lab
    rows = [
        {
            "link_id": link,
            "timestamp": time.time(),
            "up": True,
            "latency_ms": 20,
            "jitter_ms": 3,
            "loss_pct": 0,
            "utilization_pct": 5,
        }
        for link in LABELS
    ]
    fixture.probe = probe_reply(rows)
    collector = FlexiWANTelemetryCollector(adapter.client)
    assert len(collector.collect()) == 3
    fixture.probe = probe_reply(rows[:2])
    assert len(collector.collect()) == 2  # One missing WAN does not hide two healthy WANs.
    for bad in (
        [{**row, "timestamp": 1} for row in rows],
        [{**row, "jitter_ms": float("nan")} for row in rows],
    ):
        fixture.probe = probe_reply(bad)
        with pytest.raises(IntegrationError):
            collector.collect()


@pytest.mark.parametrize("ahead", [0.13, 1.0, 1.001, 60.0])
def test_live_collector_waits_for_small_clock_lead_without_accepting_future(
    lab, monkeypatch, ahead
):
    from sdwan import collector as module

    fixture, adapter = lab
    now = [1000.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now[0], sleep=sleep))
    row = {
        "link_id": "wan1",
        "timestamp": 1000.0 + ahead,
        "up": True,
        "latency_ms": 20,
        "jitter_ms": 3,
        "loss_pct": 0,
        "utilization_pct": 5,
    }
    fixture.probe = probe_reply([row])
    collector = FlexiWANTelemetryCollector(adapter.client)
    if ahead <= 1:
        result = collector.collect()
        assert result == [row]  # The raw timestamp is retained.
        assert result[0]["timestamp"] <= now[0]
        assert waits == pytest.approx([ahead])
    else:
        with pytest.raises(IntegrationError, match="stale"):
            collector.collect()
        assert not waits


def test_live_collector_preserves_rejection_after_clock_moves_back(lab, monkeypatch):
    from sdwan import collector as module

    fixture, adapter = lab
    now = [1000.0]
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(time=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, 999.0)),
    )
    fixture.probe = probe_reply(
        [
            {
                "link_id": "wan1",
                "timestamp": 1000.13,
                "up": True,
                "latency_ms": 20,
                "jitter_ms": 3,
                "loss_pct": 0,
                "utilization_pct": 5,
            }
        ]
    )
    with pytest.raises(IntegrationError, match="stale"):
        FlexiWANTelemetryCollector(adapter.client).collect()


def test_vpp_probe_parser_has_measured_jitter_and_conservative_down():
    assert ping_metrics(
        "time=10 ms\ntime=30 ms\nStatistics: 2 sent, 2 received, 0% packet loss"
    ) == (True, 20, 10, 0)
    assert ping_metrics("Statistics: 2 sent, 0 received, 100% packet loss") == (
        False,
        10000,
        5000,
        100,
    )
    with pytest.raises(ValueError):
        ping_metrics("VPP not running")
    assert counters(
        "GigabitEthernet0/1/0 1 up rx bytes 123\n tx bytes 456\n", "GigabitEthernet0/1/0"
    ) == (True, 123, 456)
    with pytest.raises(ValueError):
        counters("local0 0 up", "not-present")

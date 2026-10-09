"""Exercise catalogue coverage, fault targeting and preservation of frozen v1 traces."""

import hashlib
import json
from itertools import permutations, product

import pytest

from lab.edge.probe import inject_lab_fault
from lab.impairments import BASE_DELAY, BASE_JITTER, CAPACITY, plan
from lab.run_live_eval import saturation_command
from sdwan.domain import LINKS
from sdwan.scenarios import BALANCED_SCENARIOS, NORMAL, SCENARIOS, manifest, scenario
from sdwan.simulator import trace


def test_availability_exercises_all_eight_binary_states():
    masks = set()
    for case in BALANCED_SCENARIOS:
        if case.group != "availability":
            continue
        frames = trace(case, 80000)
        frame = next((frame for frame in frames if frame.event), frames[30])
        masks.add(tuple(frame.truth[link].up for link in LINKS))
    assert masks == set(product((False, True), repeat=3))


def test_legacy_raw_traces_keep_their_frozen_meaning():
    # SHA-256 captured from the original source before moving/expanding the catalogue.
    data = [
        {
            "scenario": case.id,
            "frames": [
                {
                    "now": frame.now,
                    "truth": {link: sample.model_dump() for link, sample in frame.truth.items()},
                    "observed": frame.observed,
                    "event": frame.event,
                }
                for frame in trace(case, 80000)
            ],
        }
        for case in (*SCENARIOS, NORMAL)
    ]
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert digest == "47186bbfcb0917443ed41fb41312f40575880daf98b68e7cf2da0b29fd006933"


def test_manifest_counts_selected_cases_and_does_not_claim_full_coverage():
    full = manifest()
    assert full["group_counts"] == {
        "availability": 8,
        "quality": 15,
        "stability": 3,
        "mixed": 6,
        "telemetry": 6,
    }
    assert full["case_count"] == 38 and full["fault_case_count"] == 37
    assert full["recoverable_fault_count"] == 36
    subset = manifest(identifiers=["CONTROL", "S08-wan2"])
    assert subset["case_count"] == 2 and subset["fault_case_count"] == 1
    assert not subset["complete_suite_selected"] and subset["full_suite_case_count"] == 38
    assert subset["sha256"] != full["sha256"]
    with pytest.raises(ValueError, match="distinct"):
        manifest(identifiers=["S01", "S01"])
    with pytest.raises(ValueError, match="outside"):
        manifest("legacy", ["S08-wan2"])


@pytest.mark.parametrize(
    "case",
    [case for case in BALANCED_SCENARIOS if case.group in {"quality", "stability"}],
    ids=lambda case: case.id,
)
def test_single_wan_impairments_target_each_link_in_simulation_and_netem(case):
    target = case.links[0]
    initial = plan("CONTROL")["wans"]
    frames = trace(case, 80000)
    events = [frame for frame in frames if frame.event]
    target_samples = [frame.truth[target] for frame in events]
    injected = plan(case.id, 60)["wans"]
    for link in LINKS:
        if link != target:
            assert injected[link] == initial[link]
    if case.kind == "latency":
        assert min(sample.latency_ms for sample in target_samples) > 400
        assert injected[target]["delay_ms"] == 240
    elif case.kind == "jitter":
        assert min(sample.jitter_ms for sample in target_samples) > 130
        assert injected[target]["jitter_ms"] == 150
    elif case.kind == "loss":
        assert min(sample.loss_pct for sample in target_samples) >= 7.2
        assert injected[target]["loss_pct"] == 8
    elif case.kind == "saturation":
        assert all(sample.utilization_pct == 98 for sample in target_samples)
        injection = plan(case.id)
        assert injection["background_load_link"] == target
        assert injection["background_load_mbps"] == CAPACITY[target] * 0.98
    elif case.kind == "ramp":
        assert target_samples[-1].latency_ms > target_samples[0].latency_ms + 300
        assert injected[target]["delay_ms"] == BASE_DELAY[target] + 210
        assert injected[target]["jitter_ms"] == BASE_JITTER[target] + 70
    elif case.kind == "flap":
        assert {sample.up for sample in target_samples} == {False, True}
        assert plan(case.id, 0)["wans"][target]["loss_pct"] == 100
        assert plan(case.id, 15)["wans"][target]["loss_pct"] == 0


def test_each_single_fault_family_covers_wan1_wan2_wan3():
    for kind in ("latency", "jitter", "loss", "saturation", "ramp", "flap", "missing", "corrupt"):
        assert {case.links for case in BALANCED_SCENARIOS if case.kind == kind} == {
            (link,) for link in LINKS
        }


def test_mixed_faults_rotate_both_roles_and_leave_a_healthy_backup():
    cases = [case for case in BALANCED_SCENARIOS if case.kind == "mixed"]
    assert {case.affected_links for case in cases} == set(permutations(LINKS, 2))
    for case in cases:
        latency_link, loss_link = case.affected_links
        healthy = next(link for link in LINKS if link not in case.affected_links)
        injected = plan(case.id)["wans"]
        assert injected[latency_link]["delay_ms"] == 240
        assert injected[loss_link]["loss_pct"] == 9
        assert injected[healthy] == plan("CONTROL")["wans"][healthy]
        event = next(frame for frame in trace(case, 80000) if frame.event)
        assert event.truth[latency_link].latency_ms > 400
        assert event.truth[loss_link].loss_pct > 8
        assert event.truth[healthy].latency_ms < 100 and event.truth[healthy].loss_pct < 1


@pytest.mark.parametrize(
    "case",
    [case for case in BALANCED_SCENARIOS if case.group == "telemetry"],
    ids=lambda case: case.id,
)
def test_telemetry_fault_is_scoped_in_replay_netem_and_the_edge_marker(case):
    target = case.links[0]
    injected = plan(case.id)
    assert injected["telemetry_links"] == [target]
    event = next(frame for frame in trace(case, 80000) if frame.event)
    rows = [{"link_id": link, "loss_pct": 0} for link in LINKS]
    output = inject_lab_fault(rows, {"mode": case.kind, "links": [target], "created_at": 100}, 101)
    if case.kind == "missing":
        assert {row["link_id"] for row in event.observed} == set(LINKS) - {target}
        assert {row["link_id"] for row in output} == set(LINKS) - {target}
    else:
        assert {row["link_id"] for row in event.observed if row["loss_pct"] > 100} == {target}
        assert {row["link_id"] for row in output if row["loss_pct"] > 100} == {target}
        assert injected["wans"][target]["delay_ms"] == 240


def test_expired_or_future_markers_do_not_inject_and_unknown_scope_fails_closed():
    rows = [{"link_id": link, "loss_pct": 0} for link in LINKS]
    for now in (99, 281):
        assert (
            inject_lab_fault(rows, {"mode": "missing", "links": ["wan2"], "created_at": 100}, now)
            == rows
        )
    with pytest.raises(ValueError, match="targets"):
        inject_lab_fault(rows, {"mode": "missing", "links": ["management"], "created_at": 100}, 101)
    assert scenario("S08-wan3").affected_links == ("wan3",)
    with pytest.raises(ValueError, match="Unknown"):
        scenario("S99")


@pytest.mark.parametrize(
    "identifier,namespace,host,rate",
    [
        ("S11", "sdw-wan1", "172.16.1.1", 98),
        ("S11-wan2", "sdw-wan2", "172.16.2.1", 78.4),
        ("S11-wan3", "sdw-wan3", "172.16.3.1", 29.4),
    ],
)
def test_live_saturation_load_uses_the_target_namespace_and_its_capacity(
    tmp_path, identifier, namespace, host, rate
):
    command = saturation_command(plan(identifier), tmp_path)
    assert command[:4] == ["ip", "netns", "exec", namespace]
    assert command[command.index("--host") + 1] == host
    assert float(command[command.index("--mbps") + 1]) == pytest.approx(rate)
    assert command[command.index("--port") + 1] == "9999"


def test_control_cannot_accidentally_start_an_underlay_flood(tmp_path):
    with pytest.raises(ValueError, match="scoped"):
        saturation_command(plan("CONTROL"), tmp_path)

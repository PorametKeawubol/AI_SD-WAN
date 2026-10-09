"""Report fixtures are temporary metadata; they never claim an executed edge trial."""

import json

import pytest

from lab.report_live import report
from sdwan.scenarios import manifest


def fixture(directory, level, complete, rows):
    (directory / "results.json").write_text(
        json.dumps({"evidence_level": level, "complete": complete, "trials": rows}),
        encoding="utf-8",
    )


def row(measured=False, valid=False, passed=False):
    return {
        "scenario": "S01",
        "profile": "voice",
        "strategy": "reactive",
        "repeat": 0,
        "evidence_level": "real_packets" if measured else "live_lab_attempt",
        "packet_measurement_completed": measured,
        "valid": valid,
        "passed": passed,
    }


def test_setup_attempt_is_reported_without_packet_or_coverage_credit(tmp_path):
    fixture(tmp_path, "live_lab_attempt", False, [row()])
    result = report(tmp_path)
    assert result["evidence_level"] == "live_lab_attempt"
    summary = result["summary"][0]
    assert summary["measured_trials"] == summary["valid_trials"] == 0
    assert summary["invalid_trials"] == 1 and summary["pass_rate"] is None
    assert result["coverage"]["reactive"]["handled_count"] == 0


def test_unmeasured_attempt_cannot_be_credited_even_with_success_flags(tmp_path):
    fixture(tmp_path, "live_lab_attempt", True, [row(valid=True, passed=True)])
    result = report(tmp_path)
    assert result["summary"][0]["passed_trials"] == 0
    assert result["coverage"]["reactive"]["handled_count"] == 0


def test_incomplete_mixed_attempt_preserves_failure_and_measured_rates(tmp_path):
    fixture(tmp_path, "real_packets", False, [row(measured=True, valid=True, passed=True), row()])
    result = report(tmp_path)
    summary = result["summary"][0]
    assert summary["measured_trials"] == summary["valid_trials"] == 1
    assert summary["invalid_trials"] == 1 and summary["pass_rate"] == 1
    assert summary["wilson_95"][0] < summary["wilson_95"][1]
    assert result["coverage"]["reactive"]["handled_count"] == 0
    assert not result["coverage"]["reactive"]["all_16_all_3_profiles_evaluated"]


def test_synthetic_results_are_rejected_by_live_report(tmp_path):
    fixture(tmp_path, "synthetic", True, [row(measured=True, valid=True, passed=True)])
    with pytest.raises(ValueError, match="live lab evidence"):
        report(tmp_path)


def test_balanced_subset_counts_control_separately_and_reports_groups(tmp_path):
    rows = [
        {**row(measured=True, valid=True, passed=True), "scenario": identifier}
        for identifier in ("CONTROL", "S08-wan2", "S15-wan3")
    ]
    evidence = {
        "evidence_level": "real_packets",
        "complete": True,
        "trials": rows,
        "case_manifest": manifest(identifiers=[r["scenario"] for r in rows]),
    }
    (tmp_path / "results.json").write_text(json.dumps(evidence), encoding="utf-8")
    result = report(tmp_path)
    coverage = result["coverage"]["reactive"]
    assert coverage["handled_count"] == coverage["fault_cases_selected"] == 2
    assert coverage["normal_control_passed"] is True
    assert coverage["by_group"]["availability"] == {"case_count": 1, "passed_case_count": 1}
    assert coverage["by_group"]["quality"] == {"case_count": 1, "passed_case_count": 1}
    assert coverage["by_group"]["telemetry"] == {"case_count": 1, "passed_case_count": 1}
    assert not coverage["full_suite_all_3_profiles_evaluated"]
    assert "all_16_all_3_profiles_evaluated" not in coverage


def test_missing_declared_profile_or_repeat_cannot_receive_case_credit(tmp_path):
    evidence = {
        "evidence_level": "real_packets",
        "complete": True,
        "trials": [row(measured=True, valid=True, passed=True)],
        "case_manifest": manifest(identifiers=["S01"]),
    }
    (tmp_path / "results.json").write_text(json.dumps(evidence), encoding="utf-8")
    (tmp_path / "experiment.json").write_text(
        json.dumps({"profiles": {"voice": {}, "bulk": {}}, "repetitions": 2}), encoding="utf-8"
    )
    assert report(tmp_path)["coverage"]["reactive"]["handled_count"] == 0


def test_observed_case_outside_manifest_is_rejected(tmp_path):
    evidence = {
        "evidence_level": "real_packets",
        "complete": True,
        "trials": [row(measured=True, valid=True, passed=True)],
        "case_manifest": manifest(identifiers=["S08-wan2"]),
    }
    (tmp_path / "results.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        report(tmp_path)

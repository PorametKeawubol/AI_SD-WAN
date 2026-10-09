"""Aggregate observed live trials without mixing them with synthetic results."""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from sdwan.domain import PROFILES
from sdwan.scenarios import GROUPS, SCENARIOS
from sdwan.scenarios import scenario as resolve_scenario


def wilson(successes, count):
    if count == 0:
        return None
    z = 1.959963984540054
    p = successes / count
    center = (p + z * z / (2 * count)) / (1 + z * z / count)
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / (1 + z * z / count)
    return [center - half, center + half]


def report(directory):
    directory = Path(directory)
    evidence = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    level = evidence.get("evidence_level")
    if level not in {"real_packets", "live_lab_attempt"}:
        raise ValueError("Report requires live lab evidence, not simulator results")
    catalogue = evidence.get("case_manifest")
    selected_cases = (
        tuple(resolve_scenario(case["id"]) for case in catalogue["cases"])
        if catalogue
        else SCENARIOS
    )
    selected_ids = {case.id for case in selected_cases}
    if catalogue and any(row["scenario"] not in selected_ids for row in evidence["trials"]):
        raise ValueError("Observed case is outside the declared test selection")
    metadata_file = directory / "experiment.json"
    metadata = (
        json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.is_file() else {}
    )
    groups = defaultdict(list)
    for trial in evidence["trials"]:
        groups[(trial["scenario"], trial["profile"], trial["strategy"])].append(trial)
    summary = []
    for (scenario, profile, strategy), trials in sorted(groups.items()):
        measured = [
            row
            for row in trials
            if row.get("packet_measurement_completed") is True
            and row.get("evidence_level") == "real_packets"
            and level == "real_packets"
        ]
        valid = [row for row in measured if row.get("valid")]
        passed = sum(bool(row.get("passed")) for row in valid)
        summary.append(
            {
                "scenario": scenario,
                "group": resolve_scenario(scenario).group,
                "profile": profile,
                "strategy": strategy,
                "trials": len(trials),
                "measured_trials": len(measured),
                "valid_trials": len(valid),
                "invalid_trials": len(trials) - len(valid),
                "passed_trials": passed,
                "pass_rate": passed / len(valid) if valid else None,
                "wilson_95": wilson(passed, len(valid)),
                "profile_passed": bool(
                    evidence.get("complete")
                    and len(valid) == len(trials)
                    and valid
                    and passed / len(valid) >= 0.95
                ),
            }
        )
    profiles = {row["profile"] for row in summary}
    planned_profiles = set(metadata.get("profiles", profiles))
    repetitions = metadata.get("repetitions", 1)
    coverage = {}
    for strategy in sorted({row["strategy"] for row in summary}):
        passed_cases = []
        for scenario in (case.id for case in selected_cases):
            rows = [
                row
                for row in summary
                if row["strategy"] == strategy and row["scenario"] == scenario
            ]
            if (
                {row["profile"] for row in rows} == planned_profiles
                and rows
                and all(
                    row["profile_passed"] and row["valid_trials"] >= repetitions for row in rows
                )
            ):
                passed_cases.append(scenario)
        faults = [case for case in selected_cases if case.kind != "normal"]
        recoverable = [case for case in faults if case.recoverable]
        selection_measured = (
            bool(evidence.get("complete"))
            and planned_profiles == set(PROFILES)
            and all(
                any(
                    row["scenario"] == case.id
                    and row["profile"] == profile
                    and row["strategy"] == strategy
                    and row["measured_trials"] >= repetitions
                    for row in summary
                )
                for case in selected_cases
                for profile in planned_profiles
            )
        )
        full_suite_selected = catalogue.get("complete_suite_selected", False) if catalogue else True
        full_measured = selection_measured and full_suite_selected
        coverage[strategy] = {
            "handled_cases_observed": [case.id for case in faults if case.id in passed_cases],
            "handled_count": sum(case.id in passed_cases for case in faults),
            "fault_cases_selected": len(faults),
            "restored_count": sum(case.id in passed_cases for case in recoverable),
            "recoverable_cases_selected": len(recoverable),
            "normal_control_passed": "CONTROL" in passed_cases
            if "CONTROL" in selected_ids
            else None,
            "all_selected_cases_all_3_profiles_evaluated": selection_measured,
            "full_suite_all_3_profiles_evaluated": full_measured,
            "by_group": {
                group: {
                    "case_count": sum(case.group == group for case in selected_cases),
                    "passed_case_count": sum(
                        case.group == group and case.id in passed_cases for case in selected_cases
                    ),
                }
                for group in GROUPS
            },
        }
        if not catalogue or catalogue["suite"] == "legacy":
            coverage[strategy]["all_16_all_3_profiles_evaluated"] = (
                full_measured and {case.id for case in SCENARIOS} <= selected_ids
            )
    output = {
        "evidence_level": level,
        "complete": evidence.get("complete", False),
        "coverage": coverage,
        "summary": summary,
        "native_qbr_evaluated": evidence.get("native_qbr_evaluated", False),
        **({"case_manifest": catalogue} if catalogue else {}),
    }
    (directory / "summary.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    with (directory / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]) if summary else ["scenario"])
        writer.writeheader()
        writer.writerows(summary)
    text = [
        "# Live flexiWAN packet evaluation",
        "",
        "Measured UDP RTT and RTT standard deviation are proxies, not one-way latency/media jitter.",
        "",
        "Evidence level: " + level,
        "",
        "Packet measurements completed: " + str(sum(row["measured_trials"] for row in summary)),
        "",
        "Invalid harness trials remain visible and do not count as successful routing.",
        "",
        "Native QBR evaluated: " + str(output["native_qbr_evaluated"]),
        "",
        "| Strategy | Fault cases passing / selected | SLA maintained / eligible | Full suite × 3 profiles measured |",
        "|---|---:|---:|---|",
    ]
    for strategy, row in coverage.items():
        text.append(
            f"| {strategy} | {row['handled_count']} / {row['fault_cases_selected']} | {row['restored_count']} / {row['recoverable_cases_selected']} | {row['full_suite_all_3_profiles_evaluated']} |"
        )
    text += [
        "",
        "## Coverage by test group",
        "",
        "| Strategy | Group | Passed / selected cases |",
        "|---|---|---|",
    ]
    for strategy, totals in coverage.items():
        for group, counts in totals["by_group"].items():
            text.append(
                f"| {strategy} | {group} | {counts['passed_case_count']} / {counts['case_count']} |"
            )
    text += [
        "",
        "Full per-profile pass rates and Wilson intervals are in summary.json/summary.csv.",
        "These results must not be combined with reports/latest (synthetic benchmark).",
    ]
    (directory / "EVALUATION.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/live")
    arguments = parser.parse_args()
    print(json.dumps(report(arguments.output)["coverage"], indent=2))

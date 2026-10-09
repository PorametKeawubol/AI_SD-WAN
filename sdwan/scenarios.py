"""Versioned test cases: eight binary states plus selected impairment coverage."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from itertools import permutations

from .domain import LINKS

GROUPS = ("availability", "quality", "stability", "mixed", "telemetry")
SUITES = ("balanced", "legacy")


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    kind: str
    links: tuple[str, ...]
    recoverable: bool = True

    @property
    def group(self):
        if self.kind in {"normal", "down"}:
            return "availability"
        if self.kind in {"latency", "jitter", "loss", "saturation", "ramp"}:
            return "quality"
        if self.kind == "flap":
            return "stability"
        if self.kind in {"missing", "corrupt"}:
            return "telemetry"
        return "mixed"

    @property
    def affected_links(self):
        # The original mixed case lists all WANs but only impairs the first two.
        return self.links[:2] if self.kind == "mixed" else self.links


# Preserve IDs and definitions used by the frozen v1 experiments.
SCENARIOS = (
    Scenario("S01", "WAN1 down", "down", ("wan1",)),
    Scenario("S02", "WAN2 down", "down", ("wan2",)),
    Scenario("S03", "WAN3 down", "down", ("wan3",)),
    Scenario("S04", "WAN1 + WAN2 down", "down", ("wan1", "wan2")),
    Scenario("S05", "WAN1 + WAN3 down", "down", ("wan1", "wan3")),
    Scenario("S06", "WAN2 + WAN3 down", "down", ("wan2", "wan3")),
    Scenario("S07", "All links down", "down", LINKS, False),
    Scenario("S08", "High latency", "latency", ("wan1",)),
    Scenario("S09", "High jitter", "jitter", ("wan1",)),
    Scenario("S10", "Packet loss", "loss", ("wan1",)),
    Scenario("S11", "Saturation", "saturation", ("wan1",)),
    Scenario("S12", "Progressive degradation", "ramp", ("wan1",)),
    Scenario("S13", "Link flapping", "flap", ("wan1",)),
    Scenario("S14", "Concurrent mixed impairments", "mixed", LINKS),
    Scenario("S15", "Missing/stale telemetry", "missing", ("wan1",)),
    Scenario("S16", "Corrupt telemetry", "corrupt", ("wan1",)),
)
NORMAL = Scenario("CONTROL", "Normal control", "normal", ())


def _balanced():
    cases = [NORMAL, *SCENARIOS[:7]]
    for base in SCENARIOS[7:]:
        cases.append(base)
        if base.kind == "mixed":
            for latency_link, loss_link in permutations(LINKS, 2):
                if (latency_link, loss_link) != ("wan1", "wan2"):
                    cases.append(
                        replace(
                            base,
                            id=f"{base.id}-{latency_link}-{loss_link}",
                            name=f"{latency_link.upper()} latency + {loss_link.upper()} loss",
                            links=(latency_link, loss_link),
                        )
                    )
        else:
            for link in LINKS[1:]:
                cases.append(
                    replace(
                        base,
                        id=f"{base.id}-{link}",
                        name=f"{link.upper()}: {base.name}",
                        links=(link,),
                    )
                )
    return tuple(cases)


BALANCED_SCENARIOS = _balanced()
CATALOGUE = {case.id: case for case in BALANCED_SCENARIOS}


def scenario(identifier):
    try:
        return CATALOGUE[identifier]
    except KeyError:
        raise ValueError("Unknown scenario: " + str(identifier)) from None


def suite_cases(suite="balanced", *, legacy_control=True):
    if suite == "balanced":
        return BALANCED_SCENARIOS
    if suite == "legacy":
        return (*SCENARIOS, NORMAL) if legacy_control else SCENARIOS
    raise ValueError("Unknown suite: " + str(suite))


def manifest(suite="balanced", identifiers=None, *, legacy_control=True):
    expected = suite_cases(suite, legacy_control=legacy_control)
    selected = (
        tuple(scenario(identifier) for identifier in identifiers)
        if identifiers is not None
        else expected
    )
    if not selected or len({case.id for case in selected}) != len(selected):
        raise ValueError("Select distinct nonempty test cases")
    if not {case.id for case in selected} <= {case.id for case in expected}:
        raise ValueError("Selected case is outside the requested suite")
    groups = Counter(case.group for case in selected)
    data = {
        "suite": suite,
        "version": 2 if suite == "balanced" else 1,
        "case_count": len(selected),
        "full_suite_case_count": len(expected),
        "full_suite_fault_count": sum(case.kind != "normal" for case in expected),
        "fault_case_count": sum(case.kind != "normal" for case in selected),
        "recoverable_fault_count": sum(
            case.kind != "normal" and case.recoverable for case in selected
        ),
        "group_counts": {group: groups[group] for group in GROUPS},
        "complete_suite_selected": {case.id for case in selected} == {case.id for case in expected},
        "initial_priority": "wan1",
        "scope": "Balanced v2 covers all 8 binary Up/Down states. Other impairments are selected tests. Legacy v1 retains the original definitions.",
        "cases": [
            {**asdict(case), "group": case.group, "affected_links": list(case.affected_links)}
            for case in selected
        ],
    }
    data["sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return data

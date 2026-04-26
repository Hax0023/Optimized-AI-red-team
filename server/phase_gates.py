"""
Phase gate validation — enforces minimum test coverage before phase advancement.
Gates are intentionally strict: missing a gate is better than missing a vuln class.
"""

import json
from pathlib import Path
from typing import Optional

PHASE_REQUIREMENTS: dict[int, dict] = {
    0: {
        "name": "Discovery",
        "mandatory_tools": ["nmap", "ffuf", "nuclei", "whatweb", "katana"],
        "min_endpoints": 3,
    },
    1: {
        "name": "Information Gathering",
        "categories": ["INFO"],
        "min_tests": 5,
        "must_pass": ["INFO-01", "INFO-02", "INFO-06", "INFO-09"],
    },
    2: {
        "name": "Configuration Testing",
        "categories": ["CONF"],
        "min_tests": 6,
        "must_pass": ["CONF-01", "CONF-02", "CONF-04", "CONF-05", "CONF-07"],
    },
    3: {
        "name": "Authentication / Authorization / Session",
        "categories": ["ATHN", "ATHZ", "SESS"],
        "min_tests": 10,
        "must_pass": ["ATHN-01", "ATHN-02", "ATHZ-01", "SESS-01", "SESS-06"],
    },
    4: {
        "name": "Input Validation",
        "categories": ["INPV"],
        "min_tests": 8,
        "must_pass": ["INPV-01", "INPV-02", "INPV-05", "INPV-07", "INPV-11"],
        "core_exploits": ["xss", "sqli", "cmdi", "ssti", "ssrf"],
        "min_core_exploits": 3,
    },
    5: {
        "name": "Error / Crypto / Business Logic / Client-Side",
        "categories": ["ERRH", "CRYP", "BUSL", "CLNT"],
        "min_tests": 8,
        "must_pass": ["ERRH-01", "CRYP-01", "BUSL-01", "CLNT-01"],
    },
}

QA_REVIEW_REQUIRED_PHASES = {1, 2, 3, 4, 5}


def check_phase_gate(
    phase: int,
    tested: dict[str, str],    # test_id → status (pass/fail/na/skip)
    tool_coverage: dict[str, str],  # tool_name → status
    findings: list[dict],
    qa_review_done: bool = False,
) -> dict:
    """
    Returns: {pass: bool, blockers: [...], suggestions: [...], coverage_pct: float}
    """
    if phase not in PHASE_REQUIREMENTS:
        return {"pass": True, "blockers": [], "suggestions": [], "coverage_pct": 100.0}

    req = PHASE_REQUIREMENTS[phase]
    blockers = []
    suggestions = []

    # Tool coverage check (phase 0)
    if "mandatory_tools" in req:
        missing_tools = [
            t for t in req["mandatory_tools"]
            if tool_coverage.get(t, "pending") not in ("run", "not_applicable")
        ]
        if missing_tools:
            blockers.append(f"Mandatory tools not run: {', '.join(missing_tools)}")

        endpoint_count = len([t for t in tested if t.startswith("ENDPOINT")])
        if endpoint_count < req.get("min_endpoints", 0):
            blockers.append(
                f"Only {endpoint_count} endpoints discovered; need at least {req['min_endpoints']}"
            )

    # Category test coverage
    if "categories" in req:
        relevant = {tid: s for tid, s in tested.items()
                    if any(tid.startswith(cat) for cat in req["categories"])}
        attempted = {tid for tid, s in relevant.items() if s not in ("pending",)}
        passed_or_na = {tid for tid, s in relevant.items() if s in ("pass", "na", "skip")}

        if len(attempted) < req.get("min_tests", 0):
            blockers.append(
                f"Phase {phase} requires {req['min_tests']} tests attempted; "
                f"only {len(attempted)} attempted"
            )

        # Must-pass tests
        for must in req.get("must_pass", []):
            status = tested.get(must, "pending")
            if status == "pending":
                blockers.append(f"Must-attempt test not started: {must}")
            elif status == "fail":
                suggestions.append(
                    f"{must} failed — verify finding was logged or mark N/A with justification"
                )

        # Core exploit coverage (phase 4)
        if "core_exploits" in req:
            covered = {
                exploit for exploit in req["core_exploits"]
                if any(
                    exploit.lower() in (f.get("vuln_class", "") + f.get("title", "")).lower()
                    for f in findings
                )
            }
            if len(covered) < req["min_core_exploits"]:
                missing = set(req["core_exploits"]) - covered
                blockers.append(
                    f"Core exploit coverage: {len(covered)}/{len(req['core_exploits'])} — "
                    f"missing: {', '.join(missing)}"
                )

    # QA review check
    if phase in QA_REVIEW_REQUIRED_PHASES and not qa_review_done:
        blockers.append("QA reviewer subagent not spawned or review not recorded for this phase")

    # Coverage percentage
    total = len(tested)
    completed = len([s for s in tested.values() if s != "pending"])
    coverage_pct = (completed / total * 100) if total else 0.0

    # Suggestions
    if not blockers:
        na_count = len([s for s in tested.values() if s == "na"])
        if na_count > total * 0.5 and total > 0:
            suggestions.append(
                f"High N/A rate ({na_count}/{total}) — verify each N/A has documented justification"
            )

        zero_finding_categories = _find_zero_coverage_categories(findings, req.get("categories", []))
        for cat in zero_finding_categories:
            suggestions.append(
                f"Category {cat} has 0 findings — confirm all tests were genuinely N/A or re-examine"
            )

    return {
        "pass": len(blockers) == 0,
        "phase": phase,
        "phase_name": req.get("name", f"Phase {phase}"),
        "blockers": blockers,
        "suggestions": suggestions,
        "coverage_pct": round(coverage_pct, 1),
        "tests_attempted": completed,
        "tests_total": total,
    }


def _find_zero_coverage_categories(findings: list[dict], categories: list[str]) -> list[str]:
    """Categories that appear in requirements but have zero findings (suspicious)."""
    zero = []
    for cat in categories:
        cat_findings = [f for f in findings if cat.lower() in f.get("vuln_class", "").lower()]
        if not cat_findings:
            zero.append(cat)
    return zero

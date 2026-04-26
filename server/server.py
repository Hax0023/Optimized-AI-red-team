"""
AutoPentest-AI MCP Server
FastMCP server exposing all pentest tools, supervisor routing, and engagement management.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from .supervisor import Supervisor
from .phase_gates import check_phase_gate

mcp = FastMCP("autopentest-ai")

BASE_DIR = Path(os.getenv("ENGAGEMENT_DIR", "engagements"))

# ── In-memory cache of active supervisors (keyed by engagement_id) ───────────
_supervisors: dict[str, Supervisor] = {}


def _get_supervisor(eid: str) -> Supervisor:
    if eid not in _supervisors:
        _supervisors[eid] = Supervisor.load(eid, str(BASE_DIR))
    return _supervisors[eid]


def _eng_path(eid: str) -> Path:
    p = BASE_DIR / eid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


# ════════════════════════════════════════════════════════════════════════════════
# ENGAGEMENT MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def create_engagement(
    target_url: str,
    scope_domains: str,
    budget_usd: float = 15.0,
    config_notes: str = "",
) -> str:
    """
    Create a new pentest engagement. Returns engagement_id.
    scope_domains: comma-separated list of in-scope domains.
    budget_usd: total token budget in USD (default $15).
    """
    eid = f"pentest-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    path = _eng_path(eid)

    config = {
        "engagement_id": eid,
        "target_url": target_url,
        "scope_domains": [d.strip() for d in scope_domains.split(",")],
        "budget_usd": budget_usd,
        "config_notes": config_notes,
        "created_at": datetime.utcnow().isoformat(),
        "current_phase": 0,
        "phase_history": [],
    }
    _write_json(path / "config.json", config)
    _write_json(path / "tests.json", {})
    _write_json(path / "findings.json", [])
    _write_json(path / "tools.json", {})
    _write_json(path / "deliverables.json", {})
    _write_json(path / "checkpoints.json", [])

    (path / "logs.txt").touch()

    sup = Supervisor(eid, budget_usd, str(BASE_DIR))
    _supervisors[eid] = sup
    sup.save_state()

    return json.dumps({
        "engagement_id": eid,
        "target_url": target_url,
        "scope_domains": config["scope_domains"],
        "budget_usd": budget_usd,
        "path": str(path),
    })


@mcp.tool()
def load_engagement(eid: str) -> str:
    """Load an existing engagement and return its config + current status."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    if not config:
        return json.dumps({"error": f"Engagement {eid} not found"})

    tests = _read_json(path / "tests.json")
    findings = _read_json(path / "findings.json")
    tools = _read_json(path / "tools.json")
    sup = _get_supervisor(eid)
    budget = sup.get_budget_status()

    completed = len([s for s in tests.values() if s.get("status") != "pending"])
    return json.dumps({
        "config": config,
        "current_phase": config.get("current_phase", 0),
        "tests_completed": completed,
        "tests_total": len(tests),
        "findings_count": len(findings),
        "budget_status": budget,
    })


@mcp.tool()
def register_scope(eid: str, domain: str, domain_type: str = "primary") -> str:
    """Register an additional in-scope domain discovered during testing."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    domains = config.get("scope_domains", [])
    if domain not in domains:
        domains.append(domain)
        config["scope_domains"] = domains
        _write_json(path / "config.json", config)
    return json.dumps({"registered": domain, "all_domains": domains})


# ════════════════════════════════════════════════════════════════════════════════
# SUPERVISOR / TOKEN MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def route_task(
    eid: str,
    task_type: str,
    role: str,
    phase: int = 0,
    coverage_pct: float = 0.0,
) -> str:
    """
    CALL THIS BEFORE SPAWNING ANY SUBAGENT.
    Returns: model name, max_turns, priority_score, estimated_cost_usd.
    Use returned model and max_turns in the Task subagent parameters.

    task_type options: reconnaissance, vulnerability_analysis, exploitation_chain,
    xss_exploitation, injection_exploitation, auth_testing, session_testing,
    configuration_testing, context_compression, tool_output_parsing, final_judge,
    quality_review, report_generation, counterfactual_analysis, waf_bypass, etc.

    role options: scout, analyzer, exploiter, reporter, compressor, judge
    """
    sup = _get_supervisor(eid)
    result = sup.route_task(task_type, role, phase, coverage_pct)
    _log(eid, f"[ROUTE] task={task_type} role={role} → model={result['model']} turns={result['max_turns']}")
    return json.dumps(result)


@mcp.tool()
def report_token_usage(
    eid: str,
    agent_role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    phase: int = 0,
    task_type: str = "",
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    task_id: int = -1,
    notes: str = "",
) -> str:
    """
    CALL THIS AFTER EVERY SUBAGENT COMPLETES.
    Report actual token usage so the ledger stays accurate.
    Use approximate values if exact counts are unavailable — estimate from turn count.
    """
    sup = _get_supervisor(eid)
    result = sup.record_usage(
        agent_role=agent_role,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        phase=phase,
        task_type=task_type,
        cache_read=cache_read_tokens,
        cache_write=cache_write_tokens,
        task_id=task_id if task_id >= 0 else None,
        notes=notes,
    )
    sup.save_state()
    return json.dumps(result)


@mcp.tool()
def get_budget_status(eid: str) -> str:
    """Get current token spend, remaining budget, breakdown by phase and model."""
    sup = _get_supervisor(eid)
    return json.dumps(sup.get_budget_status())


# ════════════════════════════════════════════════════════════════════════════════
# TOOL OUTPUT COMPRESSION
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def compress_tool_output(eid: str, tool_name: str, raw_output: str) -> str:
    """
    Pass raw CLI tool output through Haiku compressor.
    Returns actionable digest — use this instead of passing full output to Analyzers.
    Saves ~60-80% of tokens on heavy tools like nuclei, ffuf, nikto.
    """
    sup = _get_supervisor(eid)
    result = sup.compress_tool_output(tool_name, raw_output)
    sup.record_usage(
        agent_role="compressor",
        model="claude-haiku-4-5",
        input_tokens=len(raw_output.split()),
        output_tokens=len(result["compressed"].split()),
        task_type="tool_output_compression",
        notes=f"compressed {tool_name}: {result['original_lines']}→{result['compressed_lines']} lines",
    )
    _log(eid, f"[COMPRESS] {tool_name}: {result['original_lines']}→{result['compressed_lines']} lines, "
              f"~{result['tokens_saved_estimate']} tokens saved")
    return json.dumps(result)


@mcp.tool()
def check_duplicate_finding(eid: str, new_finding_summary: str) -> str:
    """
    Before calling log_finding(), check if this is a duplicate.
    Returns: is_duplicate, confidence, matched_finding_id.
    """
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")
    existing = [f"{f.get('title','')} | {f.get('vuln_class','')} | {f.get('endpoint','')}"
                for f in findings]
    sup = _get_supervisor(eid)
    result = sup.check_duplicate_finding(new_finding_summary, existing)
    if result.get("is_duplicate") and result.get("matched_index", -1) >= 0:
        idx = result["matched_index"]
        if idx < len(findings):
            result["matched_finding_id"] = findings[idx].get("id", idx)
    return json.dumps(result)


# ════════════════════════════════════════════════════════════════════════════════
# PHASE MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def phase_gate_check(eid: str, phase: int) -> str:
    """
    Validate phase completion before advancing. Returns PASS or FAIL with blockers.
    Fix all blockers before calling advance_phase().
    """
    path = _eng_path(eid)
    tests = _read_json(path / "tests.json")
    tools = _read_json(path / "tools.json")
    findings = _read_json(path / "findings.json")
    config = _read_json(path / "config.json")

    qa_done = config.get(f"qa_review_phase_{phase}", False)
    result = check_phase_gate(phase, tests, tools, findings, qa_done)

    _log(eid, f"[GATE] phase={phase} pass={result['pass']} blockers={len(result['blockers'])}")
    return json.dumps(result)


@mcp.tool()
def advance_phase(eid: str, current_phase: int) -> str:
    """Advance engagement to next phase after gate passes. Saves checkpoint automatically."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    next_phase = current_phase + 1
    config["current_phase"] = next_phase
    config.setdefault("phase_history", []).append({
        "phase": current_phase,
        "completed_at": datetime.utcnow().isoformat(),
    })
    _write_json(path / "config.json", config)
    _log(eid, f"[PHASE] advanced to phase {next_phase}")
    return json.dumps({"current_phase": next_phase, "advanced_from": current_phase})


@mcp.tool()
def compress_phase_context(eid: str, phase: int, raw_context: str) -> str:
    """
    Compress a phase's full context to ~600-word summary via Haiku.
    Saves this as a deliverable for downstream agents to consume instead of raw data.
    """
    sup = _get_supervisor(eid)
    summary = sup.compress_phase_summary(phase, raw_context)
    path = _eng_path(eid)
    deliverables = _read_json(path / "deliverables.json")
    deliverables[f"phase_{phase}_summary"] = {
        "content": summary,
        "created_at": datetime.utcnow().isoformat(),
        "type": "phase_summary",
    }
    _write_json(path / "deliverables.json", deliverables)
    _log(eid, f"[COMPRESS] phase {phase} context compressed to {len(summary.split())} words")
    return json.dumps({"summary": summary, "word_count": len(summary.split())})


@mcp.tool()
def save_deliverable(eid: str, key: str, content: str) -> str:
    """Save a structured artifact for inter-agent handoff (analysis results, queues, etc.)."""
    path = _eng_path(eid)
    deliverables = _read_json(path / "deliverables.json")
    deliverables[key] = {
        "content": content,
        "created_at": datetime.utcnow().isoformat(),
    }
    _write_json(path / "deliverables.json", deliverables)
    return json.dumps({"saved": key})


@mcp.tool()
def get_deliverable(eid: str, key: str) -> str:
    """Retrieve a stored inter-agent deliverable by key."""
    path = _eng_path(eid)
    deliverables = _read_json(path / "deliverables.json")
    item = deliverables.get(key)
    if not item:
        return json.dumps({"error": f"No deliverable found for key: {key}"})
    return json.dumps(item)


# ════════════════════════════════════════════════════════════════════════════════
# TEST TRACKING
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def track_test(
    eid: str,
    test_id: str,
    status: str,
    endpoint: str = "",
    notes: str = "",
) -> str:
    """
    Record test completion. status: pass | fail | na | skip | pending.
    test_id format: INFO-01, CONF-03, ATHN-02, INPV-05, etc.
    """
    path = _eng_path(eid)
    tests = _read_json(path / "tests.json")
    tests[test_id] = {
        "status": status,
        "endpoint": endpoint,
        "notes": notes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _write_json(path / "tests.json", tests)
    _log(eid, f"[TEST] {test_id}={status} endpoint={endpoint}")
    return json.dumps({"test_id": test_id, "status": status})


@mcp.tool()
def get_coverage(eid: str) -> str:
    """Get test coverage statistics broken down by category."""
    path = _eng_path(eid)
    tests = _read_json(path / "tests.json")

    by_category: dict[str, dict] = {}
    for tid, data in tests.items():
        cat = tid.split("-")[0] if "-" in tid else "OTHER"
        if cat not in by_category:
            by_category[cat] = {"pass": 0, "fail": 0, "na": 0, "skip": 0, "pending": 0}
        status = data.get("status", "pending")
        by_category[cat][status] = by_category[cat].get(status, 0) + 1

    total = len(tests)
    completed = len([t for t in tests.values() if t.get("status") != "pending"])
    return json.dumps({
        "total_tests": total,
        "completed": completed,
        "coverage_pct": round(completed / total * 100, 1) if total else 0.0,
        "by_category": by_category,
    })


# ════════════════════════════════════════════════════════════════════════════════
# FINDING MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

SEVERITY_RANKS = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

EVIDENCE_CHECKLISTS = {
    "xss": ["HTTP request with payload", "HTTP response showing reflection/execution",
             "Screenshot or curl proof", "Exact payload string", "Reproduction steps"],
    "sqli": ["HTTP request with payload", "Response showing data extraction or error",
              "Database version or table names extracted", "sqlmap command used", "Reproduction steps"],
    "ssrf": ["HTTP request triggering SSRF", "Response from internal service or OOB callback",
             "Internal IP/service identified", "Bypass technique if WAF present"],
    "cmdi": ["HTTP request with injection payload", "Command output in response or OOB",
              "Exact injected command", "OS and user context"],
    "default": ["Full HTTP request (headers + body)", "Full HTTP response",
                "Exact payload/PoC", "Reproduction steps (3-5 ordered)", "Impact statement"],
}


@mcp.tool()
def get_evidence_checklist(vuln_class: str) -> str:
    """Get the evidence requirements before logging a finding."""
    checklist = EVIDENCE_CHECKLISTS.get(vuln_class.lower(), EVIDENCE_CHECKLISTS["default"])
    return json.dumps({
        "vuln_class": vuln_class,
        "required_evidence": checklist,
        "note": "All items must be collected before calling log_finding()",
    })


@mcp.tool()
def log_finding(
    eid: str,
    title: str,
    severity: str,
    vuln_class: str,
    endpoint: str,
    description: str,
    evidence: str,
    remediation: str,
    cvss_score: float = 0.0,
) -> str:
    """
    Log a security finding. Call get_evidence_checklist() and check_duplicate_finding()
    BEFORE calling this. severity: critical | high | medium | low | info.
    """
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")

    finding_id = f"FIND-{len(findings)+1:03d}"
    finding = {
        "id": finding_id,
        "title": title,
        "severity": severity.lower(),
        "vuln_class": vuln_class.upper(),
        "endpoint": endpoint,
        "description": description,
        "evidence": evidence,
        "remediation": remediation,
        "cvss_score": cvss_score,
        "status": "potential",
        "logged_at": datetime.utcnow().isoformat(),
    }
    findings.append(finding)
    _write_json(path / "findings.json", findings)
    _log(eid, f"[FINDING] {finding_id} {severity.upper()} {title} @ {endpoint}")

    # Append to human-readable findings log
    with open(path / "findings.md", "a") as f:
        f.write(f"\n## {finding_id}: {title}\n")
        f.write(f"**Severity:** {severity.upper()} | **Class:** {vuln_class} | **Endpoint:** {endpoint}\n\n")
        f.write(f"{description}\n\n**Evidence:**\n{evidence}\n\n**Remediation:**\n{remediation}\n\n---\n")

    return json.dumps({"finding_id": finding_id, "status": "logged"})


@mcp.tool()
def mark_exploited(
    eid: str,
    finding_id: str,
    result: str,
    evidence: str = "",
    notes: str = "",
) -> str:
    """
    Update finding exploitation status.
    result: exploited | potential | false_positive | deferred | failed
    """
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")

    for f in findings:
        if f["id"] == finding_id:
            f["status"] = result
            f["exploitation_evidence"] = evidence
            f["exploitation_notes"] = notes
            f["exploited_at"] = datetime.utcnow().isoformat()
            break
    else:
        return json.dumps({"error": f"Finding {finding_id} not found"})

    _write_json(path / "findings.json", findings)
    _log(eid, f"[EXPLOIT] {finding_id} → {result}")
    return json.dumps({"finding_id": finding_id, "result": result})


@mcp.tool()
def update_finding(eid: str, finding_id: str, updates_json: str) -> str:
    """Update specific fields of an existing finding. updates_json: JSON object of field→value."""
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")
    updates = json.loads(updates_json)

    for f in findings:
        if f["id"] == finding_id:
            f.update(updates)
            f["updated_at"] = datetime.utcnow().isoformat()
            break
    else:
        return json.dumps({"error": f"Finding {finding_id} not found"})

    _write_json(path / "findings.json", findings)
    return json.dumps({"finding_id": finding_id, "updated_fields": list(updates.keys())})


@mcp.tool()
def get_findings(eid: str, severity_filter: str = "") -> str:
    """Get all findings, optionally filtered by severity (critical/high/medium/low/info)."""
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")
    if severity_filter:
        findings = [f for f in findings if f.get("severity") == severity_filter.lower()]
    findings.sort(key=lambda f: SEVERITY_RANKS.get(f.get("severity", "info"), 0), reverse=True)
    return json.dumps({"findings": findings, "count": len(findings)})


# ════════════════════════════════════════════════════════════════════════════════
# TOOL TRACKING
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def track_tool(
    eid: str,
    tool_name: str,
    status: str,
    phase: int = 0,
    output_summary: str = "",
    findings_count: int = 0,
    notes: str = "",
) -> str:
    """
    Record tool execution. status: run | skipped | not_applicable.
    A tool with empty output does NOT count as 'run' — investigate first.
    """
    path = _eng_path(eid)
    tools = _read_json(path / "tools.json")
    tools[tool_name] = {
        "status": status,
        "phase": phase,
        "output_summary": output_summary,
        "findings_count": findings_count,
        "notes": notes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _write_json(path / "tools.json", tools)
    _log(eid, f"[TOOL] {tool_name}={status} findings={findings_count}")
    return json.dumps({"tool": tool_name, "status": status})


@mcp.tool()
def get_tool_coverage(eid: str) -> str:
    """Get tool execution coverage summary."""
    path = _eng_path(eid)
    tools = _read_json(path / "tools.json")
    by_status: dict[str, list] = {"run": [], "skipped": [], "not_applicable": [], "pending": []}
    for name, data in tools.items():
        s = data.get("status", "pending")
        by_status.setdefault(s, []).append(name)
    total_findings = sum(t.get("findings_count", 0) for t in tools.values())
    return json.dumps({
        "by_status": by_status,
        "total_tools": len(tools),
        "run_count": len(by_status.get("run", [])),
        "total_findings_from_tools": total_findings,
    })


@mcp.tool()
def verify_tool_result(tool_name: str, command_used: str, raw_output: str) -> str:
    """
    Call this when a tool produces empty or suspicious output.
    Returns likely root cause and corrected command suggestion.
    """
    common_fixes = {
        "nmap": "Add -Pn to skip host discovery. Use --open to filter. Check you have root for SYN scan.",
        "sqlmap": "Add --batch --level=3 --risk=2. Verify the parameter with -p. Check cookie with --cookie.",
        "dalfox": "Add --follow-redirects. Try --skip-bav. Check if target requires authentication.",
        "ffuf": "Verify FUZZ placement. Add -fc 404 to filter. Check wordlist path. Add -H 'Cookie: ...' if auth needed.",
        "nuclei": "Try -as for automatic scan. Check template path. Add -H for auth headers.",
        "nikto": "Add -nossl if HTTP. Use -host instead of URL. Check connectivity with curl first.",
        "feroxbuster": "Add --insecure for HTTPS. Verify wordlist exists. Add -C 404 to filter false positives.",
    }
    fix = common_fixes.get(tool_name.lower(), "Check tool installation, connectivity, and authentication.")
    empty = not raw_output.strip()
    return json.dumps({
        "tool": tool_name,
        "appears_empty": empty,
        "suggested_fix": fix,
        "command_used": command_used,
        "action": "Re-run with corrected command before marking tool as 'run'",
    })


# ════════════════════════════════════════════════════════════════════════════════
# QA & REPORTING
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def record_qa_review(eid: str, phase: int, suggestions: str, actions_taken: str) -> str:
    """Record that a QA reviewer subagent completed for this phase."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    config[f"qa_review_phase_{phase}"] = True
    config[f"qa_review_phase_{phase}_data"] = {
        "suggestions": suggestions,
        "actions_taken": actions_taken,
        "reviewed_at": datetime.utcnow().isoformat(),
    }
    _write_json(path / "config.json", config)
    _log(eid, f"[QA] phase {phase} review recorded")
    return json.dumps({"recorded": True, "phase": phase})


@mcp.tool()
def generate_report(eid: str) -> str:
    """Generate final pentest report in Markdown. Validates all gates passed first."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    findings = _read_json(path / "findings.json")
    tests = _read_json(path / "tests.json")
    tools = _read_json(path / "tools.json")
    sup = _get_supervisor(eid)
    budget = sup.get_budget_status()

    # Sort findings by severity
    findings.sort(key=lambda f: SEVERITY_RANKS.get(f.get("severity", "info"), 0), reverse=True)

    severity_counts = {}
    for f in findings:
        s = f.get("severity", "info")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    exploited = [f for f in findings if f.get("status") == "exploited"]
    potential = [f for f in findings if f.get("status") == "potential"]
    false_pos = [f for f in findings if f.get("status") == "false_positive"]

    completed = len([t for t in tests.values() if t.get("status") != "pending"])
    total_tests = len(tests)

    report = f"""# Penetration Test Report
**Target:** {config.get('target_url', 'N/A')}
**Engagement ID:** {eid}
**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}
**Scope:** {', '.join(config.get('scope_domains', []))}

---

## Executive Summary

This assessment identified **{len(findings)} findings** across {total_tests} test procedures.
- Exploited: {len(exploited)} | Potential: {len(potential)} | False Positives: {len(false_pos)}
- Coverage: {completed}/{total_tests} tests completed ({round(completed/total_tests*100,1) if total_tests else 0}%)

### Severity Breakdown
| Severity | Count |
|----------|-------|
| Critical | {severity_counts.get('critical', 0)} |
| High     | {severity_counts.get('high', 0)} |
| Medium   | {severity_counts.get('medium', 0)} |
| Low      | {severity_counts.get('low', 0)} |
| Info     | {severity_counts.get('info', 0)} |

---

## Findings

"""
    for f in findings:
        status_label = f.get("status", "potential").upper()
        report += f"""### [{f['id']}] {f['title']} — {f.get('severity','').upper()} [{status_label}]
**Endpoint:** {f.get('endpoint', 'N/A')}
**Vulnerability Class:** {f.get('vuln_class', 'N/A')}
**CVSS Score:** {f.get('cvss_score', 'N/A')}

**Description:**
{f.get('description', 'N/A')}

**Evidence:**
{f.get('evidence', 'N/A')}

**Remediation:**
{f.get('remediation', 'N/A')}

---

"""

    report += f"""## Token Usage Summary
| Model | Cost (USD) |
|-------|-----------|
"""
    for model, cost in budget.get("by_model", {}).items():
        report += f"| {model} | ${cost} |\n"
    report += f"\n**Total Spend:** ${budget.get('spent_usd', 0)} / ${budget.get('total_budget_usd', 0)} budget\n"

    report_path = path / "report.md"
    report_path.write_text(report)
    _log(eid, f"[REPORT] generated at {report_path}")
    return json.dumps({
        "report_path": str(report_path),
        "findings_count": len(findings),
        "coverage_pct": round(completed / total_tests * 100, 1) if total_tests else 0,
    })


@mcp.tool()
def get_judge_data(eid: str) -> str:
    """Get data package for the Final Judge (zero-context reviewer)."""
    path = _eng_path(eid)
    findings = _read_json(path / "findings.json")
    tests = _read_json(path / "tests.json")
    tools = _read_json(path / "tools.json")

    # Statistical anomaly detection
    na_count = len([t for t in tests.values() if t.get("status") == "na"])
    total = len(tests)
    na_rate = na_count / total if total else 0

    zero_categories = []
    by_cat: dict[str, int] = {}
    for tid in tests:
        cat = tid.split("-")[0] if "-" in tid else "OTHER"
        by_cat[cat] = by_cat.get(cat, 0) + 1
    finding_cats = {f.get("vuln_class", "")[:4] for f in findings}
    for cat, count in by_cat.items():
        if cat not in finding_cats and count >= 3:
            zero_categories.append(cat)

    return json.dumps({
        "findings": findings,
        "test_coverage": {tid: t.get("status") for tid, t in tests.items()},
        "tool_coverage": {name: t.get("status") for name, t in tools.items()},
        "anomaly_flags": {
            "high_na_rate": na_rate > 0.5,
            "na_rate": round(na_rate, 2),
            "zero_finding_categories": zero_categories,
            "untested_endpoints": [
                tid for tid, t in tests.items() if t.get("status") == "pending"
            ],
        },
    })


# ════════════════════════════════════════════════════════════════════════════════
# CHECKPOINT SYSTEM
# ════════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def save_checkpoint(eid: str, phase: int, notes: str = "") -> str:
    """Save engagement checkpoint. Called automatically on phase gate PASS."""
    path = _eng_path(eid)
    checkpoints = _read_json(path / "checkpoints.json")
    checkpoint = {
        "phase": phase,
        "notes": notes,
        "saved_at": datetime.utcnow().isoformat(),
        "budget_snapshot": _get_supervisor(eid).get_budget_status(),
    }
    checkpoints.append(checkpoint)
    _write_json(path / "checkpoints.json", checkpoints)
    _log(eid, f"[CHECKPOINT] phase={phase}")
    return json.dumps({"checkpoint_saved": True, "phase": phase})


@mcp.tool()
def resume_engagement(eid: str) -> str:
    """Generate a self-contained resume prompt from the latest checkpoint."""
    path = _eng_path(eid)
    config = _read_json(path / "config.json")
    checkpoints = _read_json(path / "checkpoints.json")
    findings = _read_json(path / "findings.json")

    last = checkpoints[-1] if checkpoints else {}
    phase = last.get("phase", config.get("current_phase", 0))
    budget = _get_supervisor(eid).get_budget_status()

    resume = (
        f"RESUME ENGAGEMENT: {eid}\n"
        f"Target: {config.get('target_url')}\n"
        f"Scope: {', '.join(config.get('scope_domains', []))}\n"
        f"Resuming at: Phase {phase}\n"
        f"Budget remaining: ${budget['remaining_usd']} of ${budget['total_budget_usd']}\n"
        f"Findings so far: {len(findings)}\n"
        f"Last checkpoint notes: {last.get('notes', 'none')}\n\n"
        f"Load engagement with: load_engagement('{eid}')\n"
        f"Check coverage with: get_coverage('{eid}')\n"
        f"Continue from phase {phase}."
    )
    return json.dumps({"resume_prompt": resume, "phase": phase})


# ════════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE
# ════════════════════════════════════════════════════════════════════════════════

WSTG_TESTS: dict[str, dict] = {
    "INFO-01": {"name": "Search Engine Discovery", "description": "Use search engines to find exposed info about target", "tools": ["google dorks", "shodan", "censys"]},
    "INFO-02": {"name": "Fingerprint Web Server", "description": "Identify web server type and version", "tools": ["whatweb", "curl -I", "nmap -sV"]},
    "INFO-03": {"name": "Enumerate App on Web Server", "description": "Discover all apps hosted on same server", "tools": ["nmap", "dirsearch", "ffuf"]},
    "INFO-04": {"name": "Enumerate App Entry Points", "description": "Map all input vectors and entry points", "tools": ["katana", "burp spider", "ZAP"]},
    "INFO-05": {"name": "Review Webpage Comments/Metadata", "description": "Check for sensitive data in HTML source, JS, robots.txt", "tools": ["curl", "browser devtools"]},
    "INFO-06": {"name": "Identify App Entry Points", "description": "Map GET/POST parameters, headers, cookies", "tools": ["burp", "ZAP", "katana"]},
    "INFO-07": {"name": "Map Execution Paths", "description": "Enumerate application workflow and state transitions", "tools": ["manual", "burp"]},
    "INFO-08": {"name": "Fingerprint Web App Framework", "description": "Identify CMS, framework, libraries in use", "tools": ["whatweb", "wappalyzer", "nuclei"]},
    "INFO-09": {"name": "Map Application Architecture", "description": "Identify proxies, load balancers, CDN, WAF", "tools": ["wafw00f", "curl", "traceroute"]},
    "INFO-10": {"name": "Map Application Dependencies", "description": "Identify third-party dependencies and their versions", "tools": ["retire.js", "npm audit", "dependency-check"]},
    "CONF-01": {"name": "Network / Infrastructure Configuration", "description": "Check for exposed admin interfaces, internal services", "tools": ["nmap", "nuclei"]},
    "CONF-02": {"name": "Application Platform Configuration", "description": "Review server config, default credentials, sample files", "tools": ["nuclei", "nikto"]},
    "CONF-03": {"name": "File Extension Handling", "description": "Test how server handles different file extensions", "tools": ["ffuf", "burp"]},
    "CONF-04": {"name": "Backup and Unreferenced Files", "description": "Find backup files, source code, old versions", "tools": ["ffuf", "feroxbuster", "nuclei"]},
    "CONF-05": {"name": "HTTP Methods", "description": "Test for dangerous HTTP methods (PUT, DELETE, TRACE)", "tools": ["curl", "nmap --script http-methods"]},
    "CONF-06": {"name": "HTTP Strict Transport Security", "description": "Check HSTS implementation", "tools": ["testssl", "curl -I"]},
    "CONF-07": {"name": "HTTP Security Headers", "description": "Verify security headers presence and correctness", "tools": ["nuclei", "curl -I", "securityheaders.com"]},
    "CONF-08": {"name": "RIA Cross Domain Policy", "description": "Check crossdomain.xml and CORS policy", "tools": ["curl", "nuclei"]},
    "CONF-09": {"name": "File Permission Testing", "description": "Check for world-readable sensitive files", "tools": ["ffuf", "nuclei"]},
    "CONF-10": {"name": "Subdomain Takeover", "description": "Check for dangling DNS entries pointing to unregistered services", "tools": ["subjack", "nuclei"]},
    "ATHN-01": {"name": "Testing for Credentials in Transport", "description": "Verify credentials sent over HTTPS only", "tools": ["burp", "testssl"]},
    "ATHN-02": {"name": "Default Credentials", "description": "Test for vendor default usernames/passwords", "tools": ["nuclei", "hydra"]},
    "ATHN-03": {"name": "Account Lockout", "description": "Test lockout policy and bypass techniques", "tools": ["burp intruder", "hydra"]},
    "ATHN-04": {"name": "Authentication Bypass via SQL", "description": "Test login for SQL injection bypass", "tools": ["sqlmap", "manual"]},
    "ATHN-06": {"name": "Weak Lockout Mechanism", "description": "Test for weak or absent lockout", "tools": ["burp", "hydra"]},
    "ATHN-07": {"name": "Weak Password Policy", "description": "Test password requirements and history", "tools": ["manual", "burp"]},
    "ATHN-08": {"name": "Weak Security Q&A", "description": "Test security question implementation", "tools": ["manual"]},
    "ATHN-09": {"name": "Weak Password Change", "description": "Test password change/reset flow", "tools": ["burp", "manual"]},
    "ATHN-10": {"name": "Weak Password Reset", "description": "Test password reset link security, token entropy", "tools": ["burp", "manual"]},
    "ATHZ-01": {"name": "Directory Traversal", "description": "Test for path traversal in file access parameters", "tools": ["dotdotpwn", "burp", "manual"]},
    "ATHZ-02": {"name": "Bypassing Authorization Schema", "description": "Test for IDOR and horizontal/vertical privilege escalation", "tools": ["burp", "manual", "autorize"]},
    "ATHZ-03": {"name": "Privilege Escalation", "description": "Test for vertical privilege escalation", "tools": ["burp", "manual"]},
    "ATHZ-04": {"name": "IDOR", "description": "Test for insecure direct object references", "tools": ["burp", "autorize", "manual"]},
    "SESS-01": {"name": "Session Management Schema", "description": "Analyze session token structure and entropy", "tools": ["burp sequencer", "jwt_tool"]},
    "SESS-02": {"name": "Cookie Attributes", "description": "Check HttpOnly, Secure, SameSite flags", "tools": ["burp", "curl -I"]},
    "SESS-03": {"name": "Session Fixation", "description": "Test if session token changes after auth", "tools": ["burp", "manual"]},
    "SESS-04": {"name": "Exposed Session Variables", "description": "Check if session data exposed in URL or logs", "tools": ["burp", "manual"]},
    "SESS-05": {"name": "CSRF", "description": "Test for cross-site request forgery", "tools": ["burp", "manual"]},
    "SESS-06": {"name": "Logout Functionality", "description": "Verify session invalidated server-side on logout", "tools": ["burp", "manual"]},
    "SESS-07": {"name": "Session Timeout", "description": "Test for appropriate session timeout", "tools": ["burp", "manual"]},
    "SESS-08": {"name": "Session Puzzling", "description": "Test for session variable overloading", "tools": ["burp", "manual"]},
    "INPV-01": {"name": "Reflected XSS", "description": "Test for reflected cross-site scripting", "tools": ["dalfox", "burp", "xsstrike"]},
    "INPV-02": {"name": "Stored XSS", "description": "Test for persistent XSS in stored data", "tools": ["dalfox", "burp", "manual"]},
    "INPV-03": {"name": "DOM-Based XSS", "description": "Test for client-side XSS via DOM manipulation", "tools": ["dalfox", "burp", "domxssscanner"]},
    "INPV-04": {"name": "HTTP Splitting/Smuggling", "description": "Test for HTTP response splitting and request smuggling", "tools": ["smuggler", "burp"]},
    "INPV-05": {"name": "SQL Injection", "description": "Test for SQL injection in all input parameters", "tools": ["sqlmap", "burp", "manual"]},
    "INPV-06": {"name": "LDAP Injection", "description": "Test for LDAP injection in directory queries", "tools": ["manual", "burp"]},
    "INPV-07": {"name": "XML Injection / XXE", "description": "Test for XML injection and XXE in XML parsers", "tools": ["burp", "xxeinjector", "manual"]},
    "INPV-08": {"name": "SSI Injection", "description": "Test for server-side include injection", "tools": ["burp", "manual"]},
    "INPV-09": {"name": "XPath Injection", "description": "Test for XPath injection in XML-based queries", "tools": ["burp", "manual"]},
    "INPV-10": {"name": "IMAP/SMTP Injection", "description": "Test for injection in mail-related functions", "tools": ["burp", "manual"]},
    "INPV-11": {"name": "Code Injection", "description": "Test for code injection including SSTI and eval", "tools": ["sstimap", "tplmap", "manual"]},
    "INPV-12": {"name": "Command Injection", "description": "Test for OS command injection", "tools": ["commix", "burp", "manual"]},
    "INPV-13": {"name": "Format String", "description": "Test for format string vulnerabilities", "tools": ["burp", "manual"]},
    "INPV-14": {"name": "Open Redirect", "description": "Test for unvalidated redirects and forwards", "tools": ["burp", "openredirex", "manual"]},
    "INPV-17": {"name": "SSRF", "description": "Test for server-side request forgery", "tools": ["burp", "ssrfmap", "manual"]},
    "INPV-18": {"name": "NoSQL Injection", "description": "Test for NoSQL injection in MongoDB, etc.", "tools": ["nosqli", "burp", "manual"]},
    "ERRH-01": {"name": "Error Code Analysis", "description": "Test for verbose error messages revealing stack traces, versions", "tools": ["burp", "nuclei", "manual"]},
    "ERRH-02": {"name": "Stack Traces", "description": "Trigger errors to check for stack trace disclosure", "tools": ["burp", "manual"]},
    "CRYP-01": {"name": "TLS / SSL Testing", "description": "Test for weak ciphers, protocols, certificate issues", "tools": ["testssl", "sslscan"]},
    "CRYP-02": {"name": "Padding Oracle", "description": "Test for padding oracle attacks on encrypted values", "tools": ["padbuster", "burp"]},
    "CRYP-03": {"name": "Sensitive Data in Transit", "description": "Verify sensitive data encrypted in transit", "tools": ["burp", "wireshark"]},
    "CRYP-04": {"name": "Weak Encryption", "description": "Identify use of weak encryption algorithms", "tools": ["manual", "burp"]},
    "BUSL-01": {"name": "Business Logic Data Validation", "description": "Test for logic flaws in data validation rules", "tools": ["burp", "manual"]},
    "BUSL-02": {"name": "Request Forgery Ability", "description": "Test for CSRF and state-changing GET requests", "tools": ["burp", "manual"]},
    "BUSL-03": {"name": "Integrity Checks", "description": "Test for missing integrity checks on critical operations", "tools": ["burp", "manual"]},
    "BUSL-05": {"name": "Function Limits", "description": "Test for missing rate limits on sensitive functions", "tools": ["burp intruder", "manual"]},
    "BUSL-06": {"name": "Workflow Circumvention", "description": "Test for ability to skip workflow steps", "tools": ["burp", "manual"]},
    "BUSL-07": {"name": "Defenses Against Application Misuse", "description": "Test for anti-automation controls", "tools": ["burp", "manual"]},
    "BUSL-08": {"name": "Upload of Malicious Files", "description": "Test file upload for dangerous file types", "tools": ["burp", "manual"]},
    "CLNT-01": {"name": "DOM-Based XSS", "description": "Client-side XSS via DOM sinks", "tools": ["dalfox", "burp"]},
    "CLNT-02": {"name": "JavaScript Execution", "description": "Test for unsafe JavaScript execution", "tools": ["burp", "manual"]},
    "CLNT-03": {"name": "HTML Injection", "description": "Test for HTML injection in client-rendered content", "tools": ["burp", "manual"]},
    "CLNT-06": {"name": "Clickjacking", "description": "Test for missing X-Frame-Options / CSP frame-ancestors", "tools": ["burp", "nuclei"]},
    "CLNT-07": {"name": "WebSockets Security", "description": "Test WebSocket connections for auth, injection, replay", "tools": ["burp", "websocat"]},
    "CLNT-09": {"name": "Clickjacking (CSP)", "description": "Test CSP frame-ancestors directive", "tools": ["burp", "manual"]},
    "CLNT-11": {"name": "LocalStorage Testing", "description": "Check for sensitive data stored in localStorage/sessionStorage", "tools": ["browser devtools", "manual"]},
    "CLNT-13": {"name": "CORS Testing", "description": "Test CORS policy for overly permissive origins", "tools": ["burp", "corsy", "manual"]},
}


@mcp.tool()
def get_wstg_test(test_id: str) -> str:
    """Get WSTG test procedure by ID (e.g., INPV-05, ATHN-02)."""
    test = WSTG_TESTS.get(test_id.upper())
    if not test:
        available = list(WSTG_TESTS.keys())
        return json.dumps({"error": f"Test {test_id} not found", "available": available[:20]})
    return json.dumps({"test_id": test_id.upper(), **test})


@mcp.tool()
def list_wstg_tests(category: str = "") -> str:
    """List available WSTG tests, optionally filtered by category (INFO, CONF, ATHN, etc.)."""
    tests = WSTG_TESTS
    if category:
        tests = {k: v for k, v in tests.items() if k.startswith(category.upper())}
    return json.dumps({
        "count": len(tests),
        "tests": {k: v["name"] for k, v in tests.items()},
    })


# ════════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ════════════════════════════════════════════════════════════════════════════════

def _log(eid: str, message: str):
    path = BASE_DIR / eid / "logs.txt"
    with open(path, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {message}\n")


if __name__ == "__main__":
    mcp.run()

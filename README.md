# AutoPentest-AI

Token-optimized agentic web application penetration testing framework.
Orchestrates specialist AI agents (Scout → Analyzer → Exploiter → Judge) across 7 testing phases
with a supervisory layer that routes tasks to the cheapest model capable of doing the job.

## What Makes This Different

| Feature | Standard Approach | This Framework |
|---|---|---|
| Model selection | Same model for everything | Haiku for parsing, Sonnet for analysis, Opus for exploitation |
| Token tracking | None | Real-time ledger with cost per phase and model |
| Tool output | Full stdout to agent | Haiku-compressed digest (saves 60-80%) |
| Budget control | Fixed turn limits | Dynamic turns based on remaining budget + task priority |
| Phase compression | None | ~600-word summaries replace raw phase data for downstream agents |
| Deduplication | Manual | Haiku semantic check before every `log_finding()` |

## Quick Start

### 1. Prerequisites
```bash
cp .env.example .env
# Add your Anthropic API key to .env
docker compose up -d
```

### 2. Run a pentest
```bash
cd autopentest-ai
claude   # opens Claude Code in this directory
```

Claude reads `CLAUDE.md` automatically and is ready. Tell it:
```
Pentest https://target.example.com, scope: target.example.com, budget: $15
```

### 3. What happens automatically
- Engagement created with ID `pentest-YYYYMMDD-HHMMSS`
- Scout maps the attack surface
- `route_task()` picks the right model for every subagent
- Tool output is compressed before reaching agents
- Phase gates enforce minimum coverage
- QA reviewer checks each phase
- Final Judge reviews the report with zero context
- Full token spend breakdown included in report

## Architecture

```
CLAUDE.md (orchestration methodology)
    │
    ├── route_task() ──→ Supervisor ──→ Token Ledger (SQLite)
    │                        │
    │                        └──→ Model Router (task → model mapping)
    │
    ├── compress_tool_output() ──→ Tool Compressor (Haiku)
    │
    └── MCP Server (FastMCP)
            ├── Engagement management
            ├── Phase gates
            ├── Finding management
            ├── Tool tracking
            ├── WSTG knowledge base (50+ tests)
            └── Checkpoint / resume
```

## Model Routing Logic

| Task | Default Model | Why |
|---|---|---|
| Tool output parsing | Haiku | Mechanical extraction |
| Phase compression | Haiku | Summarization, cheap |
| Dedup checking | Haiku | Similarity comparison |
| Reconnaissance | Sonnet | Analytical judgment |
| Vulnerability analysis | Sonnet | Pattern recognition |
| Auth/Session testing | Sonnet | Complex logic |
| Exploitation chains | Opus | Multi-step reasoning |
| WAF bypass | Opus | Creative problem solving |
| Final Judge | Opus | Independent critical review |

## Budget Thresholds

| Remaining | Behavior |
|---|---|
| >$10 | Full execution, Opus for exploitation |
| $5–10 | Normal, Sonnet for exploitation |
| $2–5 | Skip optional tools, reduce pipelines |
| $1–2 | Emergency Haiku mode |
| <$1 | Go straight to reporting |

## MCP Tools Reference

### Supervisor
- `route_task(eid, task_type, role, phase, coverage_pct)` → model + max_turns
- `report_token_usage(eid, role, model, input, output, phase, ...)` → cost recorded
- `get_budget_status(eid)` → full spend breakdown

### Compression
- `compress_tool_output(eid, tool_name, raw_output)` → digest
- `check_duplicate_finding(eid, summary)` → dedup check

### Engagement
- `create_engagement(target, scope, budget, notes)` → eid
- `phase_gate_check(eid, phase)` → PASS/FAIL + blockers
- `advance_phase(eid, current_phase)`
- `compress_phase_context(eid, phase, raw_context)` → summary

### Findings
- `log_finding(eid, title, severity, vuln_class, endpoint, description, evidence, remediation)`
- `mark_exploited(eid, finding_id, result, evidence)`
- `get_evidence_checklist(vuln_class)` → required evidence items

### Testing
- `track_test(eid, test_id, status, endpoint, notes)`
- `get_coverage(eid)` → coverage stats by category
- `get_wstg_test(test_id)` → test procedure

### Reporting
- `generate_report(eid)` → markdown report
- `get_judge_data(eid)` → data package for Final Judge

## Coverage

- 50+ WSTG test procedures (INFO, CONF, ATHN, ATHZ, SESS, INPV, ERRH, CRYP, BUSL, CLNT)
- 20+ pre-configured security tools in Docker
- 7-phase methodology with mandatory quality gates
- 4 role-specialized agent templates

## License

MIT

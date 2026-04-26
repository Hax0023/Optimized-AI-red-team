# Quality Gate Anti-Pattern Checklist

Used by QA Reviewer and Final Judge to detect common engagement failures.

## Red Flags — Coverage

- [ ] Any WSTG category at exactly 0% — unless target demonstrably lacks that feature
- [ ] N/A rate above 50% in any category — N/A is often used to skip hard tests
- [ ] Phase 4 (INPV) missing any of: XSS, SQLi, SSRF, CMDi, SSTI, Path Traversal
- [ ] Auth-required tests marked "skip" without documenting auth failure or asking user for credentials
- [ ] Endpoints discovered in Phase 0 that never appear in any later test record

## Red Flags — Tool Usage

- [ ] Tool marked "run" but zero findings and no notes explaining why clean output is expected
- [ ] Heavy tools (sqlmap, dalfox) with only default flags (should have --level, --tamper, etc.)
- [ ] Conditional tools (jwt_tool, graphql-cop) not evaluated when their trigger condition exists
- [ ] Empty tool output with no call to `verify_tool_result()` and no re-run attempt

## Red Flags — Finding Quality

- [ ] "High" severity finding without complete HTTP request/response evidence
- [ ] XSS finding with only canary reflection proof but no execution proof attempted
- [ ] SQLi finding without database name or version extracted
- [ ] SSRF finding without OOB callback evidence or internal service response
- [ ] Finding severity inconsistent with CVSS score (e.g., CVSS 9.8 marked "medium")
- [ ] Duplicate findings for same vulnerability on same endpoint

## Red Flags — Logic

- [ ] Phase advanced without QA reviewer spawned
- [ ] Final report generated before all phase gates passed
- [ ] Findings logged for out-of-scope endpoints
- [ ] Tests marked "pass" when they should be "fail" (e.g., no CSRF protection found)

## Scoring Guide for Recommendations

When making QA recommendations, prioritize by:
1. **Critical** — blocking issue, engagement integrity at risk
2. **High** — likely missed real vulnerability or severe finding quality gap
3. **Medium** — coverage gap or evidence weakness that may affect report accuracy
4. **Low** — minor improvement, cosmetic finding quality issue

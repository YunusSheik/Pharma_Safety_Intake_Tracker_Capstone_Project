---
name: pharma-intake-review
description: Review code, schemas, audit logic, RAG or agent changes in the Pharma Safety Intake & Triage Tracker against four checklists (data minimization, error handling, auditability, test coverage) and the non-clinical, synthetic-data, local-only contract in CLAUDE.md. Use before committing changes or when asked to review this project.
---

# Pharma Intake Review

A structured review for this repository. It complements CLAUDE.md, which holds the always-on rules. This skill holds the detailed review procedure, which only matters while reviewing.

## Inputs
- **Scope.** Use the paths the user names. Otherwise use `git diff --name-only HEAD`, plus untracked files from `git status --porcelain`. With no git history, review all of `pharma_intake_tracker/src/`, `app.py`, `scripts/`, `tests/` and `.claude/hooks/`.
- **Contract.** `CLAUDE.md` sections 2 to 6.

## Procedure
1. List the files in scope and give a one-line role for each.
2. Work through each checklist in order and record a finding for every failed item:
   1. `checklists/data-minimization.md`
   2. `checklists/error-handling.md`
   3. `checklists/auditability.md`
   4. `checklists/test-coverage.md`
3. Run the scope-boundary sweep. Grep the scope for any of these and confirm each hit is a refusal, guardrail or test, not a capability:
   - causality: `caus`, `related to`
   - treatment: `treat`, `dose adjust`
   - reportability: `reportab`, `expedited`, `seriousness`
   - network calls: `requests`, `httpx`, `urllib`, `socket`
   - secrets: `api_key`, `sk-`
4. Run the tests: `cd pharma_intake_tracker && python -m pytest -q`. Record the pass, fail and skip counts.
5. Write the report, most severe findings first.

## Severity
- **Blocker:** breaks a scope limit (clinical decision, real PII, network or cloud use, mutable audit), or a test fails.
- **Major:** a requirement isn't enforced (missing validation, a missing audit event, an untested rule).
- **Minor:** clarity, naming or a small gap with no compliance impact.

## Output format
```
## Review — <date> — scope: <n files>
| # | Severity | Checklist | Location | Issue | Fix |
|---|----------|-----------|----------|-------|-----|
...
### Checklist verdicts
- Data minimization: PASS/FAIL
- Error handling: PASS/FAIL
- Auditability: PASS/FAIL
- Test coverage: PASS/FAIL
- Scope boundary sweep: PASS/FAIL
### Tests: <passed>/<failed>/<skipped>
### Overall: PASS / PASS WITH MINORS / FAIL
```
Refer to field and function names only. Never quote report narratives, chat text or anything that looks like personal data in the review.

## Evidence
Append the report to `docs/review-evidence.md` with the date, scope and outcome. If you fixed any findings, add a short "Resolution" line for each, naming the commit or test that proves the fix.

# Checklist — Auditability

## Coverage. Each of these state changes writes exactly one audit row:
- [ ] `REFERENCE_RESERVED`: a REF ID is reserved when a report is started.
- [ ] `REPORT_CREATED`: a report is saved, with its type and initial status.
- [ ] `VALIDATION_FAILED`: a submission is rejected. Only the field *names* and the error count are recorded.
- [ ] `STATUS_CHANGED`: a report moves to a new status (from/to).
- [ ] `CLOSURE_REQUESTED`: the HITL gate opens and the report is frozen.
- [ ] `CLOSURE_APPROVED` / `CLOSURE_REJECTED`: the human decision, with the reviewer ID.
- [ ] `RAG_QUERY`: a query hash, its length and the result count.
- [ ] `TOOL_CALL_FAILED`, `CIRCUIT_BREAKER_TRIPPED`, `GUARDRAIL_BLOCKED`: control-plane events.

## Integrity
- [ ] `audit_log` has `BEFORE UPDATE` and `BEFORE DELETE` triggers that abort.
- [ ] Each row stores `prev_hash` and `entry_hash`, where entry_hash is SHA-256 over the canonical JSON of the row plus prev_hash.
- [ ] `audit.verify_chain()` recomputes every hash and reports the first broken row.
- [ ] Timestamps are UTC ISO-8601 from a single clock function, which tests can inject.
- [ ] Every row has an actor ID (operator, reviewer or `SYSTEM`/`AGENT`). There are no anonymous writes.

## Traceability
- [ ] For a REF ID, the full history (status_history plus audit rows) can be reconstructed from the DB.
- [ ] A closure is never recorded without a matching `CLOSURE_APPROVED` row with a reviewer ID and attestation.
- [ ] The UI Audit Trail tab can filter by REF ID and runs the chain check on demand.
- [ ] The governance hook's `logs/edit_audit.log` exists and gets one line per tracked edit.

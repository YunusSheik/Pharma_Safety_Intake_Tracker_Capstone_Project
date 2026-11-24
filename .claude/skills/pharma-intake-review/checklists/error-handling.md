# Checklist — Error handling

## Validation errors
- [ ] Each missing mandatory field gives its own context-sensitive message, e.g. "Event onset date is required for Suspected Adverse Event reports". There is no single generic error.
- [ ] Format errors explain the expected format (for example `LOT-12345`) without echoing sensitive input.
- [ ] Date rules are enforced with clear messages:
  - no future dates;
  - onset date on or before the received date.
- [ ] If validation fails, the form keeps the user's input and the reserved REF ID. No partial row is written.

## Workflow errors
- [ ] Invalid transitions (skip, reverse, from Closed) raise `InvalidTransition`, and the UI shows the allowed next step.
- [ ] A report frozen by a pending HITL approval rejects further transitions with `ApprovalPending`.
- [ ] Approval without the attestation or without a reviewer ID is rejected with `ApprovalError`.

## Agent and tool errors
- [ ] Invalid tool arguments return a structured `ToolError` (tool, field paths, messages) and never a raw traceback.
- [ ] Safe auto-repair (case, separators, zero-padding) is applied *before* validation, and the trace records it.
- [ ] Repeated identical invalid calls trip the circuit breaker after `config.BREAKER_MAX_CONSECUTIVE_INVALID` attempts. Later calls fail with `CircuitOpenError` until the breaker is reset.
- [ ] A failure in the model or Ollama falls back to a deterministic path (fallback encoder or extractive answer) and never crashes the UI.

## Persistence
- [ ] DB writes use transactions. A failure rolls back, leaving neither a half-written report nor a missing audit row.
- [ ] SQL is always parameterised; no f-string SQL contains user values.
- [ ] Any attempt to UPDATE or DELETE `audit_log` raises a `sqlite3.IntegrityError` from the trigger, and the code never swallows it.

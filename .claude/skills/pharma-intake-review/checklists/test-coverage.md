# Checklist — Test coverage

Each of the following has at least one test that **fails if the rule is removed**.

## Schema validation (`tests/test_schemas.py`)
- [ ] Each mandatory field missing, one at a time, gives a field-specific message.
- [ ] `^LOT-\d{5}$` accepts valid lots and rejects four or six digits, a missing prefix and lowercase input (when not repaired).
- [ ] Future dates are rejected, and an onset date after the received date is rejected.
- [ ] Adverse-event-only fields are required for adverse events and not required for quality complaints.
- [ ] PII patterns (email, phone, DOB, "patient name") are rejected.

## Storage integrity (`tests/test_repository.py`, `tests/test_audit.py`, `tests/test_refgen.py`)
- [ ] REF IDs are unique, sequential per year and formatted as `REF-YYYY-NNNN`.
- [ ] Saved reports read back exactly as written, and duplicate REF IDs are rejected.
- [ ] Audit UPDATE and DELETE are blocked, and tampering is detected by `verify_chain`.
- [ ] Audit details reject non-whitelisted keys and never contain the description text.

## Workflow and HITL (`tests/test_workflow.py`)
- [ ] New → In Review → (request) → approve → Closed works.
- [ ] Skips, reversals and edits after Closed are rejected, and a pending approval freezes the report.

## Control plane (`tests/test_tools.py`, `test_breaker.py`, `test_router_guardrails.py`, `test_assistant_memory.py`)
- [ ] Auto-repair cases and structured errors are covered.
- [ ] The breaker stays closed for 2 identical invalid calls, trips on the 3rd, resets on a valid or different call, and resets manually.
- [ ] Every one of the 4 router routes, plus guardrail refusals, has a test.
- [ ] The ReAct trace records Thought, Action and Observation, and the memory is bounded.

## RAG (`tests/test_rag_stages.py`, `tests/test_rag_pipeline.py`)
- [ ] BM25 parameters are within the spec, and an exact lot or term-code query ranks the right document first.
- [ ] Chunks are at most 512 tokens with 64-token overlap.
- [ ] RRF matches `Σ 1/(k+rank)` with k = 60, using the top 50 of each list.
- [ ] The reranker drops chunks below 0.40 and returns 3 to 5 chunks.
- [ ] The end-to-end pipeline returns cited chunks, and an off-topic query returns no answer.
- [ ] With real models (`-m models`), "difficulty breathing" retrieves the dyspnea content.

## Governance and offline (`tests/test_governance_hook.py`, `tests/test_offline.py`)
- [ ] The hook writes one line per edit, ignores untracked tools and never contains content.
- [ ] The socket guard is active, no module imports `requests` or `httpx`, and the Ollama URL must be loopback.

## UI (`tests/test_app.py`)
- [ ] `AppTest` loads every tab without an exception.

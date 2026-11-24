# Capstone defense and technical reflection

## 1. Architectural boundary: what belongs in CLAUDE.md and what belongs in modular skills?

**CLAUDE.md holds rules that apply to every task and every edit.** It is loaded into every session, so it holds the invariants a coding agent must never violate, whatever it is working on:
- scope limits: non-clinical, synthetic data only, strictly local, no API keys;
- the architecture map, which says which layer owns which rule;
- coding rules: Pydantic at boundaries, never log free text, constants only in `config.py`, Python 3.10 compatibility;
- testing conventions: the socket guard, the `models` marker, temporary databases;
- fictional-data rules.

These are short and stable. If any of them were missing from the context during an edit, that edit could introduce a compliance defect.

**Skills hold procedures that matter only for a specific job, and only while doing it.** The `pharma-intake-review` skill contains:
- the review procedure;
- severity definitions;
- the output format;
- four detailed checklists (data minimization, error handling, auditability, test coverage).

Loading about 120 lines of checklists into every session would waste context and dilute the always-on rules. Keeping them modular also lets them evolve and be versioned separately, and lets them be invoked on demand ("review this diff") with a repeatable result written to `docs/review-evidence.md`.

The test I used for each line was: *"Would an edit that ignores this line be wrong, even if the edit has nothing to do with reviewing?"* If yes, it went into CLAUDE.md. If it only matters while evaluating code, it went into the skill. CLAUDE.md points to the skill (section 7), and the skill points back to CLAUDE.md as its contract, so there is no duplication.

## 2. Planning efficiency: how did Plan Mode clarify data flow and validation edge cases before coding?

Plan Mode forced the design questions to be settled while changing them was still cheap. These decisions came out of it (recorded in `docs/PLAN.md`):
- **When is a reference generated?** The spec says "upon report initiation". That meant reserving the reference in a separate table *before* validation, so the ID can be quoted to a caller and failed submissions keep it. That in turn required a `reference_reservations` table and a `REFERENCE_RESERVED` audit action, and it made sequence gaps explainable.
- **What does "frozen" mean for HITL?** It became a `pending_closure` flag, a unique partial index (one pending request per report), and a trigger that rejects any update while the report is frozen. The agent tool surface was then designed with no approve capability.
- **Validation edge cases**, enumerated up front:
  - blank strings count as missing;
  - all errors are shown at once, which means adverse-event conditional checks must run even when other fields fail;
  - onset ≤ received ≤ today;
  - adverse-event fields are *rejected* on quality complaints (data minimization);
  - `\d` in Pydantic's Rust regex matches non-ASCII digits, so the lot check uses Python `re.ASCII`.
- **Audit data minimization**: the plan fixed a key whitelist and "field names, never values" *before* any logging code existed, so no free-text audit rows ever had to be retrofitted.
- **Open choices were raised as questions rather than assumed**: real models vs a fallback, extractive vs local-LLM generation, license, git handling.

Implementation then tested the plan's assumptions, and one failed. The planned cross-encoder (`ms-marco-MiniLM-L-6-v2`) scored ≈0.0 on "difficulty breathing" vs "dyspnea", so Stage 4 would have dropped every correct semantic match. Because the plan had already fixed the threshold (0.40) and the acceptance test (the dyspnea query), the problem was caught immediately. Three candidates were evaluated and the model was replaced, recorded under "Deviations" in `docs/PLAN.md`.

## 3. Governance control: what makes hooks deterministic compared with model-driven evaluation?

The PostToolUse hook is **ordinary code executed by the harness, not by the model**:
- It runs on *every* matching tool event (`Edit|Write|MultiEdit|NotebookEdit`), whether or not the model "remembers" or "agrees" to log. The model cannot skip it, rephrase it or be argued out of it.
- **Same input, same output.** It reads a fixed allowlist of payload fields (`tool_name`, the path keys, `session_id`) and writes a fixed schema. There is no sampling, no model call and no network, and the output varies only by timestamp. `test_governance_hook.py::test_deterministic_output` proves this.
- **Content exclusion is structural, not a judgement call.** The hook never reads `content`, `old_string`, `new_string` or `edits`, so it cannot leak them. A model asked to "log edits without sensitive content" would have to *decide* what counts as sensitive every time, and could get it wrong.
- **Failure is bounded.** Every error path exits 0, so governance never blocks development. The log is append-only JSON Lines that anyone can audit with `jq`.

Model-driven evaluation, such as the review skill, is still valuable for *judgement*: is this error message context-sensitive enough, is this test meaningful? But it is probabilistic and only as reliable as the prompt and context. The project uses each where it fits:
- hooks for evidence that must always exist;
- tests and DB triggers for rules that must always hold;
- the review skill for qualitative assessment.

## 4. Multi-stage RAG precision: how does BM25 + bi-encoder + RRF + cross-encoder reduce hallucination?

Each stage covers a different failure mode, and the last two decide what the generator is allowed to see:
- **BM25** guarantees exact recall for identifiers that embeddings blur. In the demo, `LOT-88392` returns exactly the three records for that lot. The tokenizer keeps codes whole, so `LOT-88392` cannot match `LOT-88401` on the full token.
- **The bi-encoder** recovers meaning when the words differ. "difficulty breathing" retrieves the report that says "dyspnea" as the dense #1, even though BM25 has no shared content word for it.
- **RRF** merges the two rankings without comparing their incompatible score scales. A chunk ranked well by both lists rises, and with k = 60 no single list's #1 dominates.
- **The cross-encoder** reads the query and the chunk *together* and produces a calibrated relevance probability. The 0.40 threshold is the hallucination brake. Off-topic or unanswerable queries ("capital of France", "hello") keep **zero** chunks, and the system then answers "No sufficiently relevant record found" rather than letting a generator improvise.

Generation is constrained as well:
- **Extractive by default.** The default generator only *quotes* sentences from the surviving chunks, with `[S#]` tags, so every sentence in an answer exists verbatim in the local corpus. `test_extractive_answer_only_quotes_retrieved_text` checks this.
- **Grounded prompt for a local LLM.** If Ollama is enabled, the prompt forbids outside knowledge and requires citations, and an **output guardrail** still withholds any clinical or regulatory conclusion.

To be precise about the claim: this *minimizes* and *bounds* hallucination rather than mathematically eliminating it. Extractive mode can still quote a true sentence that does not fully answer the question. That is why citations are always shown, and why the per-stage diagnostics are visible in the UI.

## 5. Production readiness: what must change before production deployment?

This is a training prototype. A production version would need at least the following.

**Security and identity**
- Real authentication (SSO/OIDC, MFA) and role-based authorization. The current operator and reviewer selectors are simulated pseudonymous roles. Enforce four-eyes approval server-side (the approver differs from the requester), with segregation of duties.
- TLS everywhere, secrets in a vault, encryption at rest for the database and backups, and hardened containers with dependency and CVE scanning.
- Model supply chain: pinned model revisions with checksums, an internal model registry, and no runtime downloads.

**Compliance**
- 21 CFR Part 11 / EU Annex 11: validated system (IQ/OQ/PQ), electronic signatures bound to authenticated identities, audit trails with reason-for-change, and documented SOPs for the system itself.
- GDPR/HIPAA: real AE data contains personal health data, so the regex PII screen would need to become a DPIA-backed data-protection design (access controls, retention schedules, right-to-erasure handling that does not break audit immutability), plus a validated de-identification service.
- Integration with the validated pharmacovigilance safety database (E2B(R3) exchange) as the system of record. This tool would stay an intake front door and never make causality, seriousness or reportability decisions.
- Timeliness controls (regulatory clocks start at awareness date), with escalations and monitoring.

**Infrastructure and reliability**
- Replace SQLite with a managed RDBMS (PostgreSQL) with migrations, point-in-time recovery, HA and tested backups. Anchor the audit hash chain externally, e.g. WORM storage or a periodically published root hash.
- Move the vector index to a proper store (pgvector or similar) with incremental indexing. Serve models behind an internal inference service with GPU or batching, plus latency and quality monitoring: retrieval evaluation sets, reranker threshold calibration, drift alerts.
- Observability (structured logs without PHI, metrics, tracing), rate limiting, and CI/CD with change control, plus a formal validation package for every release.
- Guardrails: keep the deterministic rules, and add a reviewed red-team test suite and human QA sampling of assistant outputs.

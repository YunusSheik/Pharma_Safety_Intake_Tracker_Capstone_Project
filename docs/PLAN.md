> **Plan Mode output — Deliverable 3.** Generated in Claude Code Plan Mode on 2026-11-23, after inspecting the empty workspace, the case-study PDF and the reference repository layout. It was reviewed and **approved by the project owner before any application code was written**. Design decisions confirmed during planning: real local RAG models with a deterministic fallback; extractive generation with optional loopback-only Ollama; MIT license; git init plus an initial commit. Changes made during implementation are listed in the 'Deviations' section at the end.

# Plan — Pharma Safety Intake & Triage Tracker (capstone build)

## Context
You need a GitHub-ready project that implements every requirement in the "Pharma Safety Intake & Triage Tracker" case study PDF:
- the app: intake, validation, dashboard, lifecycle, audit
- the 5 AI-governance phases
- the 4-stage hybrid RAG
- the 5-step agentic control plane
- the 7 deliverables
- the 5 defense questions

The folder layout mirrors `sanjayyadavgithub/Pharma_Safety_Intake_Tracker_Capstone`, but all code is written fresh. It also fixes the gaps found in that repo:
- fake RAG models
- 57 committed `.pyc` files
- absolute machine paths in the README
- a committed demo database

The workspace (`Capstone_Project/`) is empty, and it becomes the repo root.

**Decisions confirmed by you:**
- **RAG models:** real local models (sentence-transformers + CPU torch) with a deterministic fallback for CI.
- **Answer generation:** extractive answer by default, with an optional local Ollama model (loopback only).
- **Git:** `git init` on `main` plus one local commit; you add the remote and push.
- **License:** MIT.
- **API key:** the OpenRouter key you offered will not be used. Calling OpenRouter would break the spec's "no external APIs" rule. The key goes into no file, `.env` or config, and the verification step greps the repo for `sk-or-` and other key patterns.

**Hard constraints from the spec:**
- Python 3.10+ and Streamlit.
- Storage is SQLite.
- Strictly local: no cloud, no external APIs at runtime. Models are downloaded once at setup, then loaded with `local_files_only` and `HF_HUB_OFFLINE=1`.
- Synthetic data only, with zero PII.
- Never assess causality, recommend treatment, or decide reportability.

## Repository layout (mirrors the reference)
```
.claude/settings.json                 PostToolUse hook registration (Phase V)
.claude/hooks/log_edit.py             content-free edit logger
.claude/skills/pharma-intake-review/SKILL.md + checklists/{data-minimization,error-handling,auditability,test-coverage}.md
.github/workflows/ci.yml              pytest on py3.10/3.12, fallback mode
CLAUDE.md  README.md  LICENSE  .gitignore
docs/PLAN.md (this plan, approved)  ACCEPTANCE.md  DEFENSE.md  DEMO.md  DEMO_OUTPUT.md  WORKFLOW.md  review-evidence.md
logs/edit_audit.log                   created by the hook (kept in git as evidence)
pharma_intake_tracker/
  app.py  requirements.txt  requirements-models.txt  pytest.ini  .streamlit/config.toml
  data/sops/SOP-00{1..5}_*.md         synthetic SOPs (multi-section)
  scripts/seed.py  demo_run.py  download_models.py
  src/pharma_intake/
    config.py errors.py db.py audit.py refgen.py schemas.py pii.py repository.py workflow.py tools.py
    rag/  text.py chunker.py corpus.py bm25.py dense.py rrf.py rerank.py prompt.py generator.py pipeline.py
    agent/ memory.py router.py guardrails.py breaker.py assistant.py
  tests/  conftest.py + one module per component (see Tests)
```
The `.gitignore` covers: `.venv`, `__pycache__`, `*.db`, `models/`, `.pytest_cache`, `.env`, `.claude/settings.local.json`.

## Component design

**Domain and validation** (`schemas.py`, `pii.py`, `refgen.py`)
- **Pydantic v2 models.** `IntakeReport` has these fields:
  - `report_type`: Suspected Adverse Event or Product Quality Complaint
  - `product_name`
  - `batch_lot_number`, matching `^LOT-\d{5}$`
  - `date_received`, which cannot be in the future
  - `reporter_type`: a fixed list of categories, no names
  - `intake_channel`: Email or Telephone
  - `description`: 10–2000 characters
- **Adverse-event fields.** `event_description` and `event_onset_date` are required only for adverse events. The onset date must be on or before `date_received`.
- **Error messages.** Errors are mapped to context-sensitive messages, for example "Event onset date is required for Suspected Adverse Event reports."
- **PII guard.** `pii.py` rejects free text that looks like an email, phone number, ID number, date of birth, or "patient name" pattern. The error names the field and the kind of identifier, never the value.
- **Reference IDs.** `REF-2026-XXXX` (current year plus a 4-digit sequence) is reserved atomically from a SQLite sequence table when you start a report. That is "upon report initiation", and each reservation is audited.

**Persistence = long-term memory** (`db.py`, `repository.py`, `audit.py`)
- **Tables:** `reports`, `ref_sequence`, `status_history`, `closure_approvals` and `audit_log`. All are created via stdlib `sqlite3` with WAL mode and foreign keys on.
- **Immutable audit log.** SQLite `BEFORE UPDATE/DELETE` triggers raise ABORT, and each row stores a SHA-256 hash chained to the previous row. `verify_chain()` detects tampering.
- **What the audit log stores:** timestamp, a pseudonymous operator ID (e.g. `OP-001`, `REV-01`), action, ref_id, and `details` limited to a key whitelist (status from/to, field *names*, counts, route, reason codes). It never stores descriptions or query text; queries are logged as a hash and a length.

**Workflow and HITL** (`workflow.py`)
- **Allowed transitions:** only New → In Review → Closed. Skips, reversals and edits after Closed are rejected with `InvalidTransition`.
- **HITL gate.** Moving In Review → Closed creates a pending `closure_approvals` row and freezes the report: no edits or transitions while pending.
- **Approval.** Only `approve_closure(reviewer_id, attestation=True)` can close the report, and it records the authorization in the audit trail. `reject_closure` unfreezes the report and leaves it In Review.
- **Agent restriction.** The agent's tools can *request* closure but have no approve capability.

**4-stage RAG** (`rag/`)
- **Corpus:** the SOP markdown files plus past reports from the DB. Text is split by section first, then into 512-token windows with 64-token overlap (`chunker.py`). Tokens are counted with the bi-encoder tokenizer when it's loaded, otherwise with a regex tokenizer.
- **Stage 1 — BM25.** Implemented from scratch, with k1=1.5 (validated in [1.2, 1.5]) and b=0.75. The tokenizer keeps codes such as `lot-88392` whole and also indexes their parts.
- **Stage 2 — bi-encoder.** `BAAI/bge-small-en-v1.5` (512-token max), normalized embeddings, cosine similarity, top-50. The fallback is a deterministic hashing embedder.
- **Stage 3 — RRF.** `Σ 1/(60 + rank)` over the BM25 top-50 and the dense top-50. No score normalization.
- **Stage 4 — cross-encoder.** `cross-encoder/ms-marco-MiniLM-L-6-v2` with a sigmoid applied to get a score in [0, 1]. Chunks scoring below 0.40 are dropped and the top 3–5 are kept. The fallback is a lexical-overlap scorer in [0, 1].
- **Prompt.** `prompt.py` builds the grounded, non-clinical prompt with citations from the top chunks.
- **Generation.** `generator.py` returns a cited extractive answer by default, or calls Ollama only when `PHARMA_LLM_BACKEND=ollama` and the URL resolves to loopback.
- **No-answer case.** If no chunk passes the threshold, the answer is "No sufficiently relevant record found" rather than a guess.
- **Diagnostics.** The pipeline returns per-stage results for the UI and the demo.

**Control plane** (`tools.py`, `agent/`)
- **Step 1 — type-safe tools.**
  - Tools: `submit_intake_report`, `update_status`, `get_report`, `search_knowledge`.
  - Each has a Pydantic arguments model with strict regexes.
  - Auto-repair normalizes common typos (e.g. `lot 12345` or `Lot_12345` → `LOT-12345`, `in-review` → `In Review`, `ref-2026-7` → `REF-2026-0007`). Anything else returns a structured `ToolError` listing the field paths.
- **Step 2 — dual memory.**
  - Short-term: `ShortTermMemory`, a bounded, volatile Thought/Action/Observation trace kept per chat session.
  - Long-term: the SQLite repository.
- **Step 3 — deterministic router.** This runs before any tool or LLM call.
  - Ordered rules, then a fixed-threshold semantic match against example phrases for each intent (using the loaded encoder or the fallback).
  - Four routes: Direct Response, Hybrid RAG, Intake Form Tool, Status Workflow Manager.
  - The same input always gives the same route.
- **Step 4 — HITL.** As described under Workflow; the UI has a reviewer approvals queue.
- **Step 5 — breaker and guardrails.**
  - The `CircuitBreaker` signs each call as tool name plus a hash of its canonical JSON arguments.
  - If the same invalid call happens more than 2 times in a row, the breaker trips and halts the agent until it is reset.
  - A valid call or a different signature resets the count. Trips are audited.
  - Input guardrails refuse causality, treatment and reportability questions.
  - Output guardrails block clinical-decision wording.
- **Assistant.** `assistant.py` orchestrates in this order: input guardrail → router → tool (validated, breaker) → observation → answer. Every step is written to the ReAct trace.

**Streamlit UI** (`app.py`)
- **Sidebar:** pseudonymous operator selector, synthetic-data banner, model mode indicator (Real / Fallback).
- **Tabs:**
  1. New Intake: the reserved REF ID, a form with conditional adverse-event fields, and inline errors.
  2. Dashboard: a table filtered by status and type.
  3. Triage Workflow: transitions plus the HITL approvals queue.
  4. Knowledge Assistant: chat with the route badge, per-stage RAG diagnostics and the ReAct trace.
  5. Audit Trail: the table plus a chain-verification button.

**Governance phases**
- **CLAUDE.md** is written first (Phase I). It covers scope limits, the architecture map, coding rules (type hints, Pydantic at boundaries, no network, never log free text), test conventions, fictional-data rules, and what belongs in CLAUDE.md versus the skill.
- **Review skill.** `SKILL.md` gives the procedure and output format, plus the 4 checklists. Running it produces `docs/review-evidence.md`. If the new project skill doesn't load mid-session, I'll do the review by following SKILL.md and say so honestly. You can re-run `/pharma-intake-review` in a fresh session.
- **Edit hook.** It appends JSON lines (`ts`, `event`, `tool_name`, a project-relative `file_path`, `session_id`) to `logs/edit_audit.log`. It never reads content, `old_string`/`new_string` or descriptions, and it uses no model and no network. It is registered for `Edit|Write|MultiEdit|NotebookEdit` and is tested directly with sample payloads.

## Tests (`pytest`, real-model tests marked `models`, skipped when the models are missing)
- `test_schemas`: required fields, lot regex, dates, adverse-event conditionals, PII rejection
- `test_refgen`: format, uniqueness, year handling
- `test_repository`: round-trip, unique constraints, storage integrity
- `test_audit`: triggers block UPDATE/DELETE, hash chain, key whitelist, no free text stored
- `test_workflow`: valid and invalid transitions, HITL freeze, approve and reject
- `test_tools`: auto-repair, structured errors
- `test_breaker`: 2 identical invalid calls stay closed, the 3rd trips, resets work
- `test_router_guardrails`: all 4 routes, refusals
- `test_memory_assistant`: ReAct trace, end-to-end chat turns
- `test_rag_*`: BM25 parameters and exact LOT matches, 512/64 chunk sizes, RRF formula, 0.40 threshold and top-k, pipeline end-to-end, `difficulty breathing → dyspnea` (models)
- `test_hook`: the hook omits content
- `test_offline`: a socket guard in conftest blocks any non-loopback connection during the suite
- `test_app`: Streamlit `AppTest` smoke test

## Build order (Phase III milestones)
1. **Phase I:** CLAUDE.md. **Phase V:** hook plus settings, created early so later edits get logged if the session picks the hook up. **Phase IV:** skill skeleton. Save this plan as `docs/PLAN.md`.
2. **A – Skeleton:** layout, config, db, requirements, pytest.ini, `.venv`, then install dependencies and download the models.
3. **B – Intake form and schemas:** schemas, PII guard, refgen, form UI.
4. **C – Validation and persistence:** repository, immutable audit, inline errors.
5. **D – Dashboard and workflow lifecycle:** dashboard, transitions, HITL queue.
6. **RAG pipeline and control plane:** these build on D. Then the seed data and the Knowledge Assistant tab.
7. **Tests, review, docs and commit:** full test suite, review-skill pass, demo run captured to `DEMO_OUTPUT.md`, docs (ACCEPTANCE matrix, DEFENSE answers, WORKFLOW diagram, README with a deliverables table), CI, LICENSE, then `git init` and the commit.

## Verification
- `pytest`: run all tests in real-model mode locally, and in fallback mode with `PHARMA_RAG_MODE=fallback`, to mirror CI.
- `python scripts/demo_run.py`: two fictional reports, a New → In Review → HITL → Closed transition, a 4-stage RAG query with stage outputs, and audit records with chain verification. The output is saved to `docs/DEMO_OUTPUT.md`.
- `streamlit run app.py`: launch the app headless and check that it serves (HTTP 200), plus the `AppTest` smoke test.
- Before committing, grep the repo for absolute paths, PII-like strings and secrets (`sk-`, `api_key`). Also check that `git status` shows no `.pyc` or `.db` files.

---

## Deviations during implementation
| # | Planned | Implemented | Why |
|---|---|---|---|
| 1 | Stage 4 cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` | `mixedbread-ai/mxbai-rerank-xsmall-v1` (Apache-2.0, 142 MB), loaded in float32 | Evaluated against the spec's own example. ms-marco scored ≈0.00 for "difficulty breathing" vs a report that says "dyspnea", so the 0.40 threshold dropped every correct semantic match. Candidates tried on the same positive/negative pairs: mxbai-xsmall (positives 0.43–0.71, negatives ≤ 0.15), gte-modernbert-base (poor separation, negatives 0.7–0.8), bge-reranker-base (missed the synonyms). mxbai is stored in fp16, so float32 is forced for CPU speed. |
| 2 | Scripts under `data/` (reference layout) | `pharma_intake_tracker/scripts/` (`seed.py`, `demo_run.py`, `download_models.py`) | Keeps `data/` for data only (SOPs + the git-ignored DB). |
| 3 | — | Added `reference_reservations` table and DB triggers for lifecycle/HITL | Needed to make "reserve on initiation" auditable, and to enforce transitions even against direct SQL edits. |
| 4 | — | SOP disclaimer banners (`>` lines) excluded from the index | They produced a near-empty "Overview" chunk in every SOP. |
| 5 | Absolute workspace path in the Context section | Replaced with the repository-relative name | Keeps machine-specific paths out of the published repository (also enforced by a test). |
| 6 | `git init` + one local commit | `git init` on `main` only, nothing committed | No git author identity was configured on the build machine. The owner chose to make the first commit themselves rather than use an invented identity. |

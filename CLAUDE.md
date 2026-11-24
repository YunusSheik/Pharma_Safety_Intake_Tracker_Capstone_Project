# CLAUDE.md — Pharma Safety Intake & Triage Tracker

This file is the standing contract for any AI coding agent working in this repository. It holds rules that apply to **every** task. Task-specific procedures, such as the code-review checklists, live in `.claude/skills/` and load only when they are needed.

## 1. What this project is
This is a local training prototype that records fictional product-quality complaints and suspected adverse event reports. It lets staff:
- validate the mandatory fields;
- track each report through New → In Review → Closed;
- keep an immutable local audit trail;
- answer historical lookups over past reports and SOPs, using a 4-stage hybrid RAG pipeline and a deterministic agent control plane.

## 2. Non-negotiable scope limits
- **Non-clinical.** The code must NEVER:
  - assess causality ("did drug X cause Y");
  - recommend or comment on medical treatment;
  - decide regulatory reportability or seriousness;
  - present itself as a pharmacovigilance system of record.

  The guardrails in `agent/guardrails.py` refuse these requests. Never weaken them to make a test pass.
- **Synthetic data only.**
  - No real patient, reporter or HCP names.
  - No emails, phone numbers, addresses, dates of birth or medical history.
  - `pii.py` rejects identifier-like free text, and new fields with free text must go through it.
- **Strictly local.**
  - No cloud services, external APIs, telemetry or remote DBs.
  - No API keys in code, config or `.env` files.
  - Models are downloaded once by `scripts/download_models.py` and then loaded with `local_files_only=True` and `HF_HUB_OFFLINE=1`.
  - The only network target allowed is an optional Ollama server on a loopback address, and `generator.py` enforces that.
- **Never add features that store extra personal details.** Operators are pseudonymous IDs (`OP-001`, `REV-01`).

## 3. Architecture map (`pharma_intake_tracker/`)
| Layer | Module(s) | Rule |
|---|---|---|
| UI | `app.py` | Streamlit only. Holds no business logic; it calls `repository`, `workflow` and `agent.assistant`. |
| Schemas | `src/pharma_intake/schemas.py`, `pii.py` | Pydantic v2 validates all external input at the boundary. |
| Reference IDs | `refgen.py` | `REF-<year>-<4 digits>`, reserved atomically in SQLite when a report is started. |
| Persistence (long-term memory) | `db.py`, `repository.py` | Stdlib `sqlite3`, parameterised SQL only, one connection per unit of work. |
| Audit | `audit.py` | Append-only table protected by triggers plus a SHA-256 hash chain. `details` keys must be on the whitelist. |
| Lifecycle + HITL | `workflow.py` | New → In Review → Closed only. Closing requires recorded human approval. |
| RAG | `rag/` | BM25 (k1 ∈ [1.2, 1.5], b = 0.75) → bi-encoder (512/64 chunks, cosine) → RRF (top-50 each, k = 60) → cross-encoder (≥ 0.40, top 3–5). |
| Control plane | `tools.py`, `agent/` | Order is guardrail → router → validated tool call → circuit breaker → observation, with a ReAct trace in short-term memory. |

## 4. Coding guidelines
- Python 3.10+. Do not use 3.11+-only features (`StrEnum`, `Self`, `except*`). Add `from __future__ import annotations` at the top of each module.
- Type hints on every public function. Prefer small pure functions, and raise the typed errors from `errors.py`.
- Every tool call and form submission goes through a Pydantic model. Never trust raw dicts.
- **Never log, audit or print free text** (descriptions, event narratives, chat queries). Audit entries carry field *names*, counts, status values and hashes.
- Keep constants in `config.py`: the RAG parameters, thresholds, paths and the breaker limit. Tests assert these values, so change them only in `config.py`.
- Keep determinism: same input → same route, same retrieval order (ties break by chunk id), same audit hash.
- Do not add dependencies without updating `requirements.txt` or `requirements-models.txt` and the README.

## 5. Testing conventions
- Run from `pharma_intake_tracker/`: `python -m pytest`. The `pytest.ini` sets `pythonpath = src .`.
- Tests use a temporary SQLite DB from the `repo` fixture in `tests/conftest.py`. Never touch `data/tracker.db`.
- `conftest.py` installs a socket guard that blocks any non-loopback connection. A test that needs the network is a bug.
- Tests that need the real models are marked `@pytest.mark.models` and skip when the models are absent. To force the fallback path, set `PHARMA_RAG_MODE=fallback`; CI does this.
- Every bug fix or new rule needs a test. Every new audit action needs a test proving it carries no free text.
- Definition of done: `python -m pytest` passes in both model modes, and the app starts with `streamlit run app.py`.

## 6. Fictional data rules
- Products are invented (e.g. "Zentravol 50 mg tablets", "Clarivex 10 mg/mL solution"). Never use real brand names.
- Lot numbers match `^LOT-\d{5}$`.
- Narratives describe events generically ("reported mild rash after second dose"). They never identify a person.
- Seed and demo data live in `scripts/seed.py` and `scripts/demo_run.py`.

## 7. Governance in this repo
- `.claude/settings.json` registers a PostToolUse hook, `.claude/hooks/log_edit.py`. It appends a content-free record of each file edit to `logs/edit_audit.log`. Do not disable it or make it log content.
- For reviews, use the `pharma-intake-review` skill (`.claude/skills/pharma-intake-review/`) and save the results to `docs/review-evidence.md`.
- `docs/PLAN.md` holds the approved Plan Mode output. Larger changes need an updated plan first.

# Acceptance matrix — case study requirement → implementation → proof

All paths are relative to `pharma_intake_tracker/` unless they start with `/`. "Test" names the test function(s) that fail if the requirement is removed.

## 1. Business and regulatory boundaries
| Requirement | Implementation | Test |
|---|---|---|
| Fictional data only | Fictional SOPs, products and lots; `pii.py` rejects identifier-like text | `test_schemas.py::test_identifier_like_text_is_rejected_without_echoing_it`, `test_router_guardrails.py::test_seed_narratives_are_clean` |
| Never assess causality / treatment / reportability / clinical decisions | `agent/guardrails.py` input and output rules; prompt rules in `rag/prompt.py` | `test_router_guardrails.py::test_input_guardrail_blocks_*`, `::test_output_guardrail_blocks_*`, `test_rag_pipeline.py::test_output_guardrail_withholds_clinical_conclusions` |

## 2. Functional capabilities
| Requirement | Implementation | Test |
|---|---|---|
| Unique reference REF-2026-XXXX on initiation | `refgen.py`, `Repository.reserve_reference` (UI "Start new report") | `test_refgen.py` (all) |
| Categories AE / PQC | `schemas.ReportType`, DB CHECK | `test_schemas.py::test_valid_reports_pass`, `test_repository.py::test_database_constraints_reject_bad_rows` |
| Core fields (product, batch/lot, date received, reporter type, description) | `schemas.IntakeReport` (+ intake channel from the business case) | `test_schemas.py::test_each_missing_mandatory_field_is_reported` |
| AE extensions without patient identifiers | `event_description`, `event_onset_date`; PII guard | `test_schemas.py::test_ae_extension_fields_required_for_adverse_events`, `::test_pii_applies_to_event_description_and_product` |
| Mandatory validation with context-sensitive errors | `schemas.validate_intake`, `_MISSING` messages | `test_schemas.py::test_missing_messages_are_context_sensitive`, `::test_every_problem_is_reported_at_once`, `test_app_and_demo.py::test_intake_reserves_reference_validates_and_saves` |
| Dashboard with filters by status and type | `app.py` Dashboard tab, `Repository.list_reports` | `test_repository.py::test_filters_by_status_and_type` |
| Lifecycle New → In Review → Closed | `workflow.py` + DB triggers | `test_workflow.py` (all) |
| Local audit trail without extra personal details | `audit.py` (whitelist, hash chain), pseudonymous actors | `test_audit.py` (all) |

## 3. Technical stack
| Requirement | Implementation | Test |
|---|---|---|
| Python 3.10+, Streamlit | No 3.11-only features; CI matrix 3.10 / 3.12 | `/.github/workflows/ci.yml`, `test_app_and_demo.py` |
| SQLite persistence | `db.py`, `repository.py` | `test_repository.py` |
| Strictly local | Loopback-only Streamlit; models loaded offline; Ollama URL must be loopback | `test_offline_and_docs.py::test_socket_guard_blocks_external_hosts`, `::test_no_network_libraries_in_application_code`, `test_rag_pipeline.py::test_remote_llm_endpoints_refused` |
| Zero PII | `pii.py`, audit whitelist, masked ReAct trace | `test_audit.py::test_no_free_text_reaches_the_audit_log`, `test_assistant_memory.py::test_chat_intake_produces_full_react_trace` |

## 4. Engineering methodology (Phases I–V)
| Phase | Artifact | Test / evidence |
|---|---|---|
| I CLAUDE.md | `/CLAUDE.md` | `test_offline_and_docs.py::test_claude_md_covers_required_topics` |
| II Plan Mode | `/docs/PLAN.md` | approved in Plan Mode before coding |
| III Milestones A–D | A skeleton (`config`, `db`, requirements) → B schemas + form → C validation + persistence → D dashboard + lifecycle | build order in `/docs/PLAN.md`; edit sequence in `/logs/edit_audit.log` |
| IV Review skill | `/.claude/skills/pharma-intake-review/` | `test_offline_and_docs.py::test_skill_frontmatter_and_checklists`, `/docs/review-evidence.md` |
| V PostToolUse hook | `/.claude/settings.json`, `/.claude/hooks/log_edit.py` | `test_governance_hook.py` (all), `/logs/edit_audit.log` |

## 5. Multi-stage hybrid RAG
| Requirement | Implementation | Test |
|---|---|---|
| BM25, k1 1.2–1.5, b 0.75, exact codes | `rag/bm25.py`, `rag/text.py` | `test_rag_stages.py::test_bm25_*`, `::test_tokenizer_keeps_codes_whole_and_split` |
| Bi-encoder, 512/64 chunks, cosine | `rag/dense.py`, `rag/chunker.py` | `test_rag_stages.py::test_chunk_windows_are_512_with_64_overlap`, `::test_dense_index_uses_cosine_similarity`, `test_rag_pipeline.py::test_real_models_are_loaded_offline` |
| Synonyms ("difficulty breathing" → "dyspnea") | real bi-encoder + cross-encoder | `test_rag_pipeline.py::test_semantic_retrieval_matches_colloquial_to_clinical_terms` (`models`) |
| RRF top-50 + top-50, k = 60 | `rag/rrf.py` | `test_rag_stages.py::test_rrf_*` |
| Cross-encoder, threshold 0.40, top 3–5 | `rag/rerank.py` | `test_rag_stages.py::test_threshold_and_top_k`, `::test_top_k_must_be_3_to_5`, `test_rag_pipeline.py::test_real_reranker_filters_irrelevant_queries` |
| Passed to the generation prompt | `rag/prompt.py`, `rag/generator.py` | `test_rag_pipeline.py::test_prompt_is_grounded_and_tagged`, `::test_extractive_answer_only_quotes_retrieved_text` |

## 6. Agentic control plane
| Step | Implementation | Test |
|---|---|---|
| 1 Type-safe tools, `^LOT-\d{5}$`, auto-repair or structured errors | `tools.py` | `test_tools.py` (all) |
| 2 Dual memory (ReAct short-term, SQLite long-term) | `agent/memory.py`, `repository.py` | `test_assistant_memory.py::test_short_term_memory_is_bounded_and_typed`, `::test_long_term_memory_reads_sqlite` |
| 3 Deterministic router, 4 routes | `agent/router.py` | `test_router_guardrails.py::test_rule_tier_routes`, `::test_router_is_deterministic` |
| 4 HITL gate In Review → Closed | `workflow.py`, DB trigger `reports_close_requires_approval` | `test_workflow.py::test_full_lifecycle_with_hitl`, `::test_only_designated_reviewers_can_approve`, `test_tools.py::test_agent_has_no_approval_tool` |
| 5 Circuit breaker > 2 identical invalid calls | `agent/breaker.py`, `ToolExecutor._fail` | `test_breaker.py` (all), `test_assistant_memory.py::test_breaker_trips_through_chat_and_needs_human_reset` |

## 7. Deliverables
See the deliverables table in `/README.md`. Demonstration evidence: `/docs/DEMO_OUTPUT.md`, regenerated by `scripts/demo_run.py` and smoke-tested by `test_app_and_demo.py::test_demo_script_produces_all_evidence_sections`.

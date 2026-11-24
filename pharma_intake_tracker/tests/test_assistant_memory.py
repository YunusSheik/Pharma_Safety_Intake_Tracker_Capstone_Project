"""Dual memory (ReAct trace + SQLite) and end-to-end assistant turns."""
from __future__ import annotations

import pytest

from pharma_intake.agent.assistant import HALTED_REPLY, Assistant, parse_fields, parse_target_status
from pharma_intake.agent.memory import LongTermMemory, ShortTermMemory
from pharma_intake.agent.router import Route

INVALID_SUBMIT = "Submit a new report: type=AE; product=Zentravol; lot=LOT-123; description=short"


@pytest.fixture
def assistant(repo, workflow, fallback_pipeline):
    return Assistant(repo, workflow, fallback_pipeline, "OP-002")


def test_short_term_memory_is_bounded_and_typed():
    memory = ShortTermMemory(limit=5)
    memory.new_turn()
    for i in range(10):
        memory.thought(f"t{i}")
    assert len(memory) == 5 and memory.steps()[0].content == "t5"
    with pytest.raises(ValueError):
        memory._add("plan", "x")
    memory.clear()
    assert len(memory) == 0 and memory.current_turn == 0


def test_long_term_memory_reads_sqlite(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    ltm = LongTermMemory(repo)
    assert ltm.report_summary(ref)["status"] == "In Review"
    assert [h["to_status"] for h in ltm.status_history(ref)] == ["New", "In Review"]
    assert "STATUS_CHANGED" in ltm.audit_actions(ref)


def test_parsers():
    assert parse_fields("Submit: type=PQC; product = Zentravol 50 mg; lot: LOT-12345") == {
        "type": "PQC", "product": "Zentravol 50 mg", "lot": "LOT-12345"}
    assert parse_target_status("move it to in-review") == "in-review"
    assert parse_target_status("please close REF-2026-0001") == "closed"
    assert parse_target_status("status of REF-2026-0001") is None


def test_greeting_is_answered_directly(assistant):
    reply = assistant.handle("hello")
    assert reply.route is Route.DIRECT and reply.tool_results == []


def test_guardrail_blocks_before_any_tool(repo, assistant):
    reply = assistant.handle("Did Zentravol cause the dyspnea?")
    assert reply.blocked and reply.guardrail_reason == "CAUSALITY" and reply.route is None
    actions = [e.action for e in repo.audit_entries()]
    assert actions == ["GUARDRAIL_BLOCKED"]
    kinds = [s.kind for s in assistant.memory.steps(reply.turn)]
    assert "action" not in kinds and kinds[-1] == "observation"


def test_chat_intake_produces_full_react_trace(repo, assistant):
    reply = assistant.handle(
        "Submit a new report: type=pqc; product=Zentravol 50 mg tablets; lot=lot 88392; received=2026/11/20; "
        "reporter=pharmacist; channel=phone; description=Blister seal found broken on arrival at the pharmacy."
    )
    assert reply.route is Route.INTAKE and reply.tool_results[0].ok
    ref = reply.tool_results[0].data["ref_id"]
    assert repo.get_report(ref).batch_lot_number == "LOT-88392"
    kinds = [s.kind for s in assistant.memory.steps(reply.turn)]
    assert kinds[:3] == ["thought", "thought", "thought"] and "action" in kinds and kinds[-1] == "observation"
    trace = " ".join(s.content for s in assistant.memory.steps())
    assert "Blister seal found broken" not in trace          # free text masked in the trace


def test_chat_status_lookup_and_hitl_request(repo, workflow, assistant, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    lookup = assistant.handle(f"what is the status of {ref}")
    assert "In Review" in lookup.text and lookup.tool_results[0].tool == "get_report"
    close = assistant.handle(f"close {ref.lower()}")
    assert close.tool_results[0].data["pending_approval"]
    assert "agent cannot approve" in close.text
    assert repo.get_report(ref).pending_closure and repo.get_report(ref).status.value == "In Review"


def test_chat_rag_returns_cited_answer(assistant):
    reply = assistant.handle("Who can approve closing a report?")
    assert reply.route is Route.RAG and reply.rag is not None
    assert reply.rag.retrieval.final and "Sources:" in reply.text


def test_breaker_trips_through_chat_and_needs_human_reset(repo, assistant):
    replies = [assistant.handle(INVALID_SUBMIT) for _ in range(3)]
    assert [r.halted for r in replies] == [False, False, True]
    assert "Circuit breaker opened" in replies[-1].text
    after = assistant.handle("Who can approve closing a report?")
    assert after.halted and after.text == HALTED_REPLY
    assistant.reset()
    assert not assistant.handle("Who can approve closing a report?").halted

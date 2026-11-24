"""Circuit breaker: halts after MORE than 2 consecutive identical invalid calls."""
from __future__ import annotations

import pytest

from pharma_intake import config
from pharma_intake.agent.breaker import CircuitBreaker, call_signature
from pharma_intake.errors import CircuitOpenError
from pharma_intake.tools import ToolExecutor

BAD = {"ref_id": "not-a-ref", "new_status": "In Review"}


def test_spec_limit():
    assert config.BREAKER_MAX_CONSECUTIVE_INVALID == 2


def test_two_identical_failures_stay_closed_third_trips():
    breaker = CircuitBreaker()
    sig = call_signature("update_status", BAD)
    assert not breaker.record_failure(sig).is_open
    assert not breaker.record_failure(sig).is_open
    state = breaker.record_failure(sig)
    assert state.is_open and state.tripped_now and state.consecutive_invalid == 3
    with pytest.raises(CircuitOpenError):
        breaker.check()


def test_trip_is_reported_once():
    breaker = CircuitBreaker()
    sig = call_signature("t", {})
    states = [breaker.record_failure(sig) for _ in range(5)]
    assert [s.tripped_now for s in states] == [False, False, True, False, False]
    assert breaker.trip_count == 1


def test_different_signature_or_success_resets_the_streak():
    breaker = CircuitBreaker()
    a, b = call_signature("t", {"x": 1}), call_signature("t", {"x": 2})
    for sig in (a, a, b, a, a):
        breaker.record_failure(sig)
    assert not breaker.is_open
    breaker.record_success()
    breaker.record_failure(a)
    breaker.record_failure(a)
    assert not breaker.is_open


def test_manual_reset():
    breaker = CircuitBreaker()
    sig = call_signature("t", {})
    for _ in range(3):
        breaker.record_failure(sig)
    assert breaker.is_open
    breaker.reset()
    assert not breaker.is_open and breaker.state().consecutive_invalid == 0


def test_signature_ignores_key_order():
    assert call_signature("t", {"a": 1, "b": 2}) == call_signature("t", {"b": 2, "a": 1})
    assert call_signature("t", {"a": 1}) != call_signature("u", {"a": 1})


def test_executor_halts_execution_and_audits(repo, workflow, fallback_pipeline, make_report):
    executor = ToolExecutor(repo, workflow, fallback_pipeline, "OP-001")
    results = [executor.call("update_status", BAD) for _ in range(3)]
    assert [r.halted for r in results] == [False, False, True]
    assert [r.attempts for r in results] == [1, 2, 3]

    # Even a perfectly valid call is now refused until a human resets.
    ref = make_report()
    blocked = executor.call("update_status", {"ref_id": ref, "new_status": "In Review"})
    assert blocked.halted and blocked.error.code == "circuit_open"
    assert repo.get_report(ref).status.value == "New"

    actions = [e.action for e in repo.audit_entries()]
    assert actions.count("TOOL_CALL_FAILED") == 3 and actions.count("CIRCUIT_BREAKER_TRIPPED") == 1

    executor.reset_breaker()
    assert executor.call("update_status", {"ref_id": ref, "new_status": "In Review"}).ok
    assert "CIRCUIT_BREAKER_RESET" in [e.action for e in repo.audit_entries()]

"""Status lifecycle and the Human-in-the-Loop closure gate."""
from __future__ import annotations

import sqlite3

import pytest

from pharma_intake import db
from pharma_intake.errors import ApprovalError, ApprovalPending, InvalidTransition
from pharma_intake.schemas import Status
from pharma_intake.workflow import TRANSITIONS, allowed_next


def test_transition_table_matches_spec():
    assert TRANSITIONS == {
        Status.NEW: (Status.IN_REVIEW,),
        Status.IN_REVIEW: (Status.CLOSED,),
        Status.CLOSED: (),
    }
    assert allowed_next("Closed") == ()


def test_full_lifecycle_with_hitl(repo, workflow, make_report):
    ref = make_report()
    assert workflow.advance(ref, "In Review", "OP-001").applied

    outcome = workflow.advance(ref, "Closed", "OP-001")
    assert outcome.pending_approval and not outcome.applied
    frozen = repo.get_report(ref)
    assert frozen.status is Status.IN_REVIEW and frozen.pending_closure          # state frozen

    closed = workflow.approve_closure(ref, "REV-01", attestation=True)
    assert closed.applied and repo.get_report(ref).status is Status.CLOSED
    assert not repo.get_report(ref).pending_closure

    actions = [e.action for e in repo.audit_entries(ref_id=ref)]
    assert actions == [
        "REFERENCE_RESERVED", "REPORT_CREATED", "STATUS_CHANGED",
        "CLOSURE_REQUESTED", "CLOSURE_APPROVED", "STATUS_CHANGED",
    ]
    approval = [e for e in repo.audit_entries(ref_id=ref) if e.action == "CLOSURE_APPROVED"][0]
    assert approval.actor == "REV-01" and approval.details == {"decision": "APPROVED", "attestation": True}
    assert [h["to_status"] for h in repo.status_history(ref)] == ["New", "In Review", "Closed"]


@pytest.mark.parametrize("start,target", [("New", "Closed"), ("New", "New")])
def test_skips_and_noops_rejected(workflow, make_report, start, target):
    ref = make_report()
    with pytest.raises(InvalidTransition, match="Allowed next status: In Review"):
        workflow.advance(ref, target, "OP-001")


def test_backwards_transition_rejected(workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    with pytest.raises(InvalidTransition):
        workflow.advance(ref, "New", "OP-001")


def test_closed_is_final(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.advance(ref, "Closed", "OP-001")
    workflow.approve_closure(ref, "REV-02", attestation=True)
    for target in ("New", "In Review", "Closed"):
        with pytest.raises(InvalidTransition, match="Closed is final"):
            workflow.advance(ref, target, "OP-001")


def test_pending_request_freezes_report(workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.request_closure(ref, "OP-001")
    with pytest.raises(ApprovalPending):
        workflow.advance(ref, "Closed", "OP-002")
    with pytest.raises(ApprovalPending):
        workflow.advance(ref, "New", "OP-002")
    assert workflow.next_actions(ref) == ()


@pytest.mark.parametrize("who", ["OP-001", "AGENT", "SYSTEM", "REV-99"])
def test_only_designated_reviewers_can_approve(repo, workflow, make_report, who):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.request_closure(ref, "OP-001")
    with pytest.raises(ApprovalError):
        workflow.approve_closure(ref, who, attestation=True)
    assert repo.get_report(ref).pending_closure


def test_attestation_is_mandatory(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.request_closure(ref, "OP-001")
    with pytest.raises(ApprovalError, match="attestation"):
        workflow.approve_closure(ref, "REV-01", attestation=False)
    assert repo.get_report(ref).status is Status.IN_REVIEW


def test_approve_without_request_rejected(workflow, make_report):
    ref = make_report()
    with pytest.raises(ApprovalError, match="no pending closure"):
        workflow.approve_closure(ref, "REV-01", attestation=True)


def test_rejection_unfreezes_and_allows_new_request(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.request_closure(ref, "OP-001")
    outcome = workflow.reject_closure(ref, "REV-01", "FOLLOW_UP_REQUIRED")
    assert outcome.to_status is Status.IN_REVIEW
    report = repo.get_report(ref)
    assert report.status is Status.IN_REVIEW and not report.pending_closure
    assert workflow.request_closure(ref, "OP-002").pending_approval
    with pytest.raises(ApprovalError):
        workflow.reject_closure(ref, "REV-01", "BECAUSE I SAID SO")


def test_database_refuses_to_close_without_approval(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    conn = db.connect(repo.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="HITL approval"):
            conn.execute("UPDATE reports SET status = 'Closed' WHERE ref_id = ?", (ref,))
        with pytest.raises(sqlite3.IntegrityError, match="illegal status transition"):
            conn.execute("UPDATE reports SET status = 'New' WHERE ref_id = ?", (ref,))
    finally:
        conn.close()


def test_database_refuses_edits_while_frozen_and_after_decision(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    workflow.request_closure(ref, "OP-001")
    conn = db.connect(repo.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            conn.execute("UPDATE reports SET product_name = 'Changed' WHERE ref_id = ?", (ref,))
    finally:
        conn.close()
    workflow.approve_closure(ref, "REV-01", attestation=True)
    conn = db.connect(repo.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="final"):
            conn.execute("UPDATE closure_approvals SET decision = 'REJECTED' WHERE ref_id = ?", (ref,))
        with pytest.raises(sqlite3.IntegrityError, match="read-only"):
            conn.execute("UPDATE reports SET product_name = 'Changed' WHERE ref_id = ?", (ref,))
    finally:
        conn.close()

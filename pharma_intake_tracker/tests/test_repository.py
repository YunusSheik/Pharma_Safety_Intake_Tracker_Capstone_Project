"""Storage integrity: round-trip, reservation rules, atomicity, DB-level constraints."""
from __future__ import annotations

import sqlite3

import pytest

from pharma_intake import audit as audit_module
from pharma_intake import db
from pharma_intake.errors import IntakeValidationError, ReferenceError_
from pharma_intake.schemas import ReportType, Status


def test_round_trip_adverse_event(repo, valid_ae):
    ref = repo.reserve_reference("OP-001")
    saved = repo.submit_report(ref, valid_ae(), "OP-001")
    again = repo.get_report(ref)
    assert saved == again
    assert again.status is Status.NEW and not again.pending_closure
    assert again.event_description == valid_ae()["event_description"]
    assert again.created_by == "OP-001"
    assert repo.status_history(ref) == [
        {"from_status": None, "to_status": "New", "changed_at": again.created_at, "changed_by": "OP-001"}
    ]


def test_reserved_reference_can_only_be_used_once(repo, valid_pqc):
    ref = repo.reserve_reference("OP-001")
    repo.submit_report(ref, valid_pqc(), "OP-001")
    with pytest.raises(ReferenceError_):
        repo.submit_report(ref, valid_pqc(), "OP-001")
    assert len(repo.list_reports()) == 1


def test_unreserved_reference_rejected(repo, valid_pqc):
    with pytest.raises(ReferenceError_):
        repo.submit_report("REF-2026-0999", valid_pqc(), "OP-001")


def test_validation_failure_writes_no_report_and_audits_field_names(repo, valid_pqc):
    ref = repo.reserve_reference("OP-001")
    with pytest.raises(IntakeValidationError):
        repo.submit_report(ref, valid_pqc(batch_lot_number="LOT-1", description="UNIQUE-NARRATIVE-MARKER"), "OP-001")
    assert repo.list_reports() == []
    failed = [e for e in repo.audit_entries(ref_id=ref) if e.action == "VALIDATION_FAILED"]
    assert failed and failed[0].details["fields"] == ["batch_lot_number"]
    assert repo.is_reference_available(ref)        # the user can fix and resubmit with the same REF


def test_filters_by_status_and_type(repo, workflow, make_report, valid_ae):
    a = make_report()
    b = make_report()
    ae_ref = repo.reserve_reference("OP-001")
    repo.submit_report(ae_ref, valid_ae(), "OP-001")
    workflow.advance(b, "In Review", "OP-001")
    assert {r.ref_id for r in repo.list_reports(statuses=["New"])} == {a, ae_ref}
    assert {r.ref_id for r in repo.list_reports(report_types=[ReportType.ADVERSE_EVENT])} == {ae_ref}
    assert [r.ref_id for r in repo.list_reports(statuses=["In Review"], report_types=["Product Quality Complaint"])] == [b]
    assert repo.status_counts() == {"New": 2, "In Review": 1, "Closed": 0}


def test_write_is_atomic_with_its_audit_row(repo, valid_pqc, monkeypatch):
    ref = repo.reserve_reference("OP-001")

    def broken_append(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(audit_module, "append", broken_append)
    with pytest.raises(RuntimeError):
        repo.submit_report(ref, valid_pqc(), "OP-001")
    monkeypatch.undo()
    assert repo.list_reports() == []
    assert repo.is_reference_available(ref)


def test_actor_must_be_pseudonymous(repo):
    with pytest.raises(Exception):
        repo.reserve_reference("jane.doe")


@pytest.mark.parametrize(
    "column,value",
    [("batch_lot_number", "LOT-12"), ("report_type", "Other"), ("status", "Archived"), ("ref_id", "REF-1")],
)
def test_database_constraints_reject_bad_rows(repo, column, value):
    row = {
        "ref_id": "REF-2026-0500", "report_type": "Product Quality Complaint", "product_name": "X",
        "batch_lot_number": "LOT-12345", "date_received": "2026-11-20", "reporter_type": "Other",
        "intake_channel": "Email", "description": "d" * 12, "status": "New", "created_at": "t",
        "created_by": "OP-001", "updated_at": "t",
    }
    row[column] = value
    conn = db.connect(repo.path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO reports({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
            )
    finally:
        conn.close()


def test_reports_cannot_be_deleted(repo, make_report):
    ref = make_report()
    conn = db.connect(repo.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM reports WHERE ref_id = ?", (ref,))
    finally:
        conn.close()
    assert repo.get_report(ref).ref_id == ref

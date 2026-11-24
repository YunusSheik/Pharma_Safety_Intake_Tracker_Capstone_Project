"""Audit trail: append-only triggers, hash chain, whitelist, no free text."""
from __future__ import annotations

import sqlite3

import pytest

from pharma_intake import audit, db
from pharma_intake.audit import ALLOWED_DETAIL_KEYS, AuditAction, query_fingerprint, validate_details
from pharma_intake.errors import AuditDetailError, IntakeValidationError
from pharma_intake.repository import Repository

from conftest import TickClock

MARKER = "Zqx7 narrative marker that must never reach the audit log"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET actor = 'OP-002'",
        "DELETE FROM audit_log",
        "UPDATE status_history SET to_status = 'Closed'",
        "DELETE FROM status_history",
    ],
)
def test_append_only_tables_reject_changes(repo, make_report, sql):
    make_report()
    conn = db.connect(repo.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
    finally:
        conn.close()


def test_chain_verifies_and_detects_tampering(repo, workflow, make_report):
    ref = make_report()
    workflow.advance(ref, "In Review", "OP-001")
    report = repo.verify_audit_chain()
    assert report.ok and report.checked == 3      # REFERENCE_RESERVED, REPORT_CREATED, STATUS_CHANGED

    # An attacker with file access drops the trigger and edits a row: the chain catches it.
    conn = sqlite3.connect(repo.path)
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET actor = 'OP-003' WHERE id = 2")
    conn.commit()
    conn.close()
    broken = repo.verify_audit_chain()
    assert not broken.ok and broken.first_bad_id == 2


def test_chain_links_each_entry_to_the_previous(repo, make_report):
    make_report()
    entries = repo.audit_entries()
    assert entries[0].prev_hash == audit.GENESIS_HASH
    for prev, cur in zip(entries, entries[1:]):
        assert cur.prev_hash == prev.entry_hash


def test_hashes_are_deterministic(tmp_path, valid_pqc):
    hashes = []
    for name in ("a.db", "b.db"):
        repo = Repository(tmp_path / name, clock=TickClock())
        ref = repo.reserve_reference("OP-001")
        repo.submit_report(ref, valid_pqc(), "OP-001")
        hashes.append(repo.audit_entries()[-1].entry_hash)
    assert hashes[0] == hashes[1]


def test_details_whitelist_enforced():
    with pytest.raises(AuditDetailError):
        validate_details({"description": "x"})
    with pytest.raises(AuditDetailError):
        validate_details({"reason": "A sentence, with punctuation, that looks like free text!"})
    with pytest.raises(AuditDetailError):
        validate_details({"reason": "x" * 65})
    clean = validate_details({"fields": ["batch_lot_number"], "stage_counts": {"bm25": 3}, "attestation": True})
    assert clean == {"fields": ["batch_lot_number"], "stage_counts": {"bm25": 3}, "attestation": True}
    assert "description" not in ALLOWED_DETAIL_KEYS and "query" not in ALLOWED_DETAIL_KEYS


def test_actor_and_ref_formats_enforced(repo):
    with repo.transaction() as conn:
        with pytest.raises(AuditDetailError):
            audit.append(conn, ts="t", actor="Jane", action=AuditAction.RAG_QUERY)
        with pytest.raises(AuditDetailError):
            audit.append(conn, ts="t", actor="OP-001", action=AuditAction.RAG_QUERY, ref_id="case 12")


def test_no_free_text_reaches_the_audit_log(repo, workflow, valid_ae):
    ref = repo.reserve_reference("OP-001")
    with pytest.raises(IntakeValidationError):
        repo.submit_report(ref, valid_ae(event_onset_date=None, description=MARKER), "OP-001")
    repo.submit_report(ref, valid_ae(description=MARKER, event_description=MARKER), "OP-001")
    workflow.advance(ref, "In Review", "OP-001")
    repo.log_event("AGENT", AuditAction.RAG_QUERY, details=query_fingerprint(MARKER))
    conn = sqlite3.connect(repo.path)
    dump = "\n".join(str(row) for row in conn.execute("SELECT * FROM audit_log"))
    conn.close()
    assert MARKER not in dump and "Zqx7" not in dump
    assert "VALIDATION_FAILED" in dump and "event_onset_date" in dump


def test_query_fingerprint_is_not_reversible_text():
    fp = query_fingerprint("Which complaints involve LOT-88392?")
    assert set(fp) == {"query_sha256", "query_length"}
    assert len(fp["query_sha256"]) == 16 and "LOT" not in fp["query_sha256"]

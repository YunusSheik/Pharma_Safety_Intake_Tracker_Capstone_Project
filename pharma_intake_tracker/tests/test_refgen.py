"""Reference generator: REF-YYYY-NNNN, unique, sequential per year, reserved on initiation."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from pharma_intake import db
from pharma_intake.errors import ReferenceError_
from pharma_intake.refgen import format_reference, is_reference, next_reference
from pharma_intake.repository import Repository

from conftest import TickClock


def test_format():
    assert format_reference(2026, 1) == "REF-2026-0001"
    assert format_reference(2026, 9999) == "REF-2026-9999"
    assert is_reference("REF-2026-0042") and not is_reference("REF-26-42")


@pytest.mark.parametrize("seq", [0, 10000])
def test_sequence_bounds(seq):
    with pytest.raises(ReferenceError_):
        format_reference(2026, seq)


def test_reservations_are_sequential_and_unique(repo):
    refs = [repo.reserve_reference("OP-001") for _ in range(25)]
    assert refs[0] == "REF-2026-0001" and refs[-1] == "REF-2026-0025"
    assert len(set(refs)) == 25
    assert all(re.fullmatch(r"REF-2026-\d{4}", r) for r in refs)


def test_reservation_is_audited_at_initiation(repo):
    ref = repo.reserve_reference("OP-002")
    entries = repo.audit_entries(ref_id=ref)
    assert [e.action for e in entries] == ["REFERENCE_RESERVED"]
    assert entries[0].actor == "OP-002"
    assert repo.is_reference_available(ref)


def test_sequence_restarts_each_year(tmp_path):
    repo = Repository(tmp_path / "y.db", clock=TickClock(datetime(2026, 12, 31, 12, 0, tzinfo=timezone.utc)))
    assert repo.reserve_reference("OP-001") == "REF-2026-0001"
    assert repo.reserve_reference("OP-001") == "REF-2026-0002"
    next_year = Repository(tmp_path / "y.db", clock=TickClock(datetime(2027, 1, 1, 9, 0, tzinfo=timezone.utc)))
    assert next_year.reserve_reference("OP-001") == "REF-2027-0001"


def test_next_reference_requires_transaction(repo):
    conn = db.connect(repo.path)
    try:
        with pytest.raises(ReferenceError_):
            next_reference(conn, 2026)
    finally:
        conn.close()


def test_sequence_exhaustion_rolls_back(repo):
    with repo.transaction() as conn:
        conn.execute("INSERT INTO ref_sequence(year, last_value) VALUES (2026, 9999)")
    with pytest.raises(ReferenceError_):
        repo.reserve_reference("OP-001")
    with repo.reader() as conn:
        assert conn.execute("SELECT last_value FROM ref_sequence WHERE year = 2026").fetchone()[0] == 9999

"""SQLite schema and connection handling (the long-term memory store).

Integrity rules are enforced twice: in Python (workflow.py, audit.py) and
again by triggers here, so a bug or a direct SQL edit cannot bypass them.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS ref_sequence (
    year        INTEGER PRIMARY KEY,
    last_value  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_reservations (
    ref_id       TEXT PRIMARY KEY,
    reserved_at  TEXT NOT NULL,
    reserved_by  TEXT NOT NULL,
    used         INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1))
);

CREATE TABLE IF NOT EXISTS reports (
    ref_id            TEXT PRIMARY KEY
                      CHECK (ref_id GLOB 'REF-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]')
                      REFERENCES reference_reservations(ref_id),
    report_type       TEXT NOT NULL
                      CHECK (report_type IN ('Suspected Adverse Event', 'Product Quality Complaint')),
    product_name      TEXT NOT NULL,
    batch_lot_number  TEXT NOT NULL CHECK (batch_lot_number GLOB 'LOT-[0-9][0-9][0-9][0-9][0-9]'),
    date_received     TEXT NOT NULL,
    reporter_type     TEXT NOT NULL,
    intake_channel    TEXT NOT NULL,
    description       TEXT NOT NULL,
    event_description TEXT,
    event_onset_date  TEXT,
    status            TEXT NOT NULL DEFAULT 'New' CHECK (status IN ('New', 'In Review', 'Closed')),
    pending_closure   INTEGER NOT NULL DEFAULT 0 CHECK (pending_closure IN (0, 1)),
    created_at        TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (report_type <> 'Suspected Adverse Event'
           OR (event_description IS NOT NULL AND event_onset_date IS NOT NULL)),
    CHECK (report_type <> 'Product Quality Complaint'
           OR (event_description IS NULL AND event_onset_date IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type);

CREATE TABLE IF NOT EXISTS status_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id       TEXT NOT NULL REFERENCES reports(ref_id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    changed_at   TEXT NOT NULL,
    changed_by   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closure_approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id        TEXT NOT NULL REFERENCES reports(ref_id),
    requested_at  TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    decision      TEXT NOT NULL DEFAULT 'PENDING'
                  CHECK (decision IN ('PENDING', 'APPROVED', 'REJECTED')),
    decided_at    TEXT,
    decided_by    TEXT,
    attestation   INTEGER NOT NULL DEFAULT 0 CHECK (attestation IN (0, 1)),
    reason        TEXT,
    CHECK (decision <> 'APPROVED' OR (attestation = 1 AND decided_by IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_pending_closure_per_report
    ON closure_approvals(ref_id) WHERE decision = 'PENDING';

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    ref_id      TEXT,
    details     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_audit_ref ON audit_log(ref_id);

-- ---------------------------------------------------------- immutability
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS status_history_no_update BEFORE UPDATE ON status_history
BEGIN SELECT RAISE(ABORT, 'status_history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS status_history_no_delete BEFORE DELETE ON status_history
BEGIN SELECT RAISE(ABORT, 'status_history is append-only'); END;

CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports
BEGIN SELECT RAISE(ABORT, 'reports cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS closure_decision_is_final BEFORE UPDATE ON closure_approvals
WHEN OLD.decision <> 'PENDING'
BEGIN SELECT RAISE(ABORT, 'closure decisions are final'); END;

CREATE TRIGGER IF NOT EXISTS closure_approvals_no_delete BEFORE DELETE ON closure_approvals
BEGIN SELECT RAISE(ABORT, 'closure approvals cannot be deleted'); END;

-- ---------------------------------------------------------- lifecycle (defence in depth)
CREATE TRIGGER IF NOT EXISTS reports_closed_is_final BEFORE UPDATE ON reports
WHEN OLD.status = 'Closed'
BEGIN SELECT RAISE(ABORT, 'closed reports are read-only'); END;

CREATE TRIGGER IF NOT EXISTS reports_frozen_while_pending BEFORE UPDATE ON reports
WHEN OLD.pending_closure = 1 AND NEW.pending_closure = 1
BEGIN SELECT RAISE(ABORT, 'report is frozen pending HITL closure approval'); END;

CREATE TRIGGER IF NOT EXISTS reports_legal_transition BEFORE UPDATE OF status ON reports
WHEN NEW.status <> OLD.status
 AND NOT ((OLD.status = 'New' AND NEW.status = 'In Review')
       OR (OLD.status = 'In Review' AND NEW.status = 'Closed'))
BEGIN SELECT RAISE(ABORT, 'illegal status transition'); END;

CREATE TRIGGER IF NOT EXISTS reports_close_requires_approval BEFORE UPDATE OF status ON reports
WHEN NEW.status = 'Closed' AND NOT EXISTS (
    SELECT 1 FROM closure_approvals
     WHERE ref_id = NEW.ref_id AND decision = 'APPROVED' AND attestation = 1)
BEGIN SELECT RAISE(ABORT, 'closing requires a recorded HITL approval'); END;
"""

PathLike = Union[str, Path]


def connect(path: PathLike) -> sqlite3.Connection:
    """Open a connection in autocommit mode; callers manage transactions explicitly."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(path: PathLike) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()

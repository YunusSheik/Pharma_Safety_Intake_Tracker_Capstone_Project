"""Repository: the long-term memory of the system (reports, history, audit).

Every write is one IMMEDIATE transaction that also writes its audit row, so
a report change can never be committed without its audit record (or the
other way round).
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from . import audit, config, db
from .audit import AuditAction, AuditEntry, ChainReport
from .errors import AuditDetailError, IntakeValidationError, ReferenceError_, ReportNotFound
from .refgen import next_reference
from .schemas import IntakeReport, ReportType, Status, StoredReport, validate_intake

_ACTOR_RE = re.compile(config.ACTOR_PATTERN, re.ASCII)
_REF_RE = re.compile(config.REF_PATTERN, re.ASCII)


def _require_actor(actor: str) -> None:
    if not _ACTOR_RE.fullmatch(actor or ""):
        raise AuditDetailError("actor must be a pseudonymous ID (OP-###, REV-##, SYSTEM or AGENT)")


def _require_ref(ref_id: str) -> None:
    if not _REF_RE.fullmatch(ref_id or ""):
        raise ReportNotFound("reference must look like REF-YYYY-NNNN")


class Repository:
    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        clock: Callable[[], datetime] = config.utc_now,
    ) -> None:
        self.path = Path(path) if path is not None else config.db_path()
        self._clock = clock
        db.init_schema(self.path)

    # ------------------------------------------------------------ plumbing
    def now_iso(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def today(self) -> date:
        return self._clock().date()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        conn = db.connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def write_audit(
        self,
        conn: sqlite3.Connection,
        actor: str,
        action: AuditAction,
        ref_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AuditEntry:
        return audit.append(conn, ts=self.now_iso(), actor=actor, action=action, ref_id=ref_id, details=details)

    def log_event(
        self,
        actor: str,
        action: AuditAction,
        ref_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AuditEntry:
        """Standalone audit event (validation failures, agent/control-plane events)."""
        with self.transaction() as conn:
            return self.write_audit(conn, actor, action, ref_id, details)

    # ------------------------------------------------------------ references
    def reserve_reference(self, actor: str, via: str = "ui") -> str:
        """Allocate a REF id when a report is initiated (spec: 'upon report initiation')."""
        _require_actor(actor)
        with self.transaction() as conn:
            ref_id = next_reference(conn, self.today().year)
            conn.execute(
                "INSERT INTO reference_reservations(ref_id, reserved_at, reserved_by) VALUES (?, ?, ?)",
                (ref_id, self.now_iso(), actor),
            )
            self.write_audit(conn, actor, AuditAction.REFERENCE_RESERVED, ref_id, {"via": via})
        return ref_id

    def is_reference_available(self, ref_id: str) -> bool:
        with self.reader() as conn:
            row = conn.execute(
                "SELECT used FROM reference_reservations WHERE ref_id = ?", (ref_id,)
            ).fetchone()
        return bool(row) and not row["used"]

    # ------------------------------------------------------------ intake
    def submit_report(
        self, ref_id: str, data: Mapping[str, Any], actor: str, via: str = "ui"
    ) -> StoredReport:
        """Validate then persist. Failures are audited by field *name* only, then re-raised."""
        _require_actor(actor)
        try:
            report = validate_intake(data, today=self.today())
        except IntakeValidationError as exc:
            self.log_event(
                actor,
                AuditAction.VALIDATION_FAILED,
                ref_id if _REF_RE.fullmatch(ref_id or "") else None,
                {"fields": exc.fields, "error_count": len(exc.issues), "via": via},
            )
            raise
        return self.create_report(ref_id, report, actor, via=via)

    def create_report(self, ref_id: str, report: IntakeReport, actor: str, via: str = "ui") -> StoredReport:
        _require_actor(actor)
        _require_ref(ref_id)
        record = report.to_record()
        now = self.now_iso()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT used FROM reference_reservations WHERE ref_id = ?", (ref_id,)
            ).fetchone()
            if row is None:
                raise ReferenceError_(f"{ref_id} was never reserved — start a new report to get a reference")
            if row["used"]:
                raise ReferenceError_(f"{ref_id} is already used by another report")
            conn.execute(
                """INSERT INTO reports(ref_id, report_type, product_name, batch_lot_number, date_received,
                       reporter_type, intake_channel, description, event_description, event_onset_date,
                       status, pending_closure, created_at, created_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', 0, ?, ?, ?)""",
                (
                    ref_id,
                    record["report_type"],
                    record["product_name"],
                    record["batch_lot_number"],
                    record["date_received"],
                    record["reporter_type"],
                    record["intake_channel"],
                    record["description"],
                    record.get("event_description"),
                    record.get("event_onset_date"),
                    now,
                    actor,
                    now,
                ),
            )
            conn.execute("UPDATE reference_reservations SET used = 1 WHERE ref_id = ?", (ref_id,))
            conn.execute(
                "INSERT INTO status_history(ref_id, from_status, to_status, changed_at, changed_by) "
                "VALUES (?, NULL, 'New', ?, ?)",
                (ref_id, now, actor),
            )
            self.write_audit(
                conn,
                actor,
                AuditAction.REPORT_CREATED,
                ref_id,
                {"report_type": record["report_type"], "status": Status.NEW.value, "via": via},
            )
        return self.get_report(ref_id)

    # ------------------------------------------------------------ reads
    @staticmethod
    def _to_report(row: sqlite3.Row) -> StoredReport:
        data = dict(row)
        data["pending_closure"] = bool(data["pending_closure"])
        return StoredReport.model_validate(data)

    def get_report(self, ref_id: str) -> StoredReport:
        with self.reader() as conn:
            row = conn.execute("SELECT * FROM reports WHERE ref_id = ?", (ref_id,)).fetchone()
        if row is None:
            raise ReportNotFound(f"No report found with reference {ref_id}")
        return self._to_report(row)

    def list_reports(
        self,
        statuses: Optional[Sequence[Union[Status, str]]] = None,
        report_types: Optional[Sequence[Union[ReportType, str]]] = None,
    ) -> List[StoredReport]:
        sql = "SELECT * FROM reports"
        clauses: List[str] = []
        params: List[Any] = []
        if statuses:
            values = [Status(s).value for s in statuses]
            clauses.append(f"status IN ({','.join('?' * len(values))})")
            params.extend(values)
        if report_types:
            values = [ReportType(t).value for t in report_types]
            clauses.append(f"report_type IN ({','.join('?' * len(values))})")
            params.extend(values)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, ref_id DESC"
        with self.reader() as conn:
            return [self._to_report(r) for r in conn.execute(sql, params).fetchall()]

    def status_history(self, ref_id: str) -> List[Dict[str, Any]]:
        with self.reader() as conn:
            rows = conn.execute(
                "SELECT from_status, to_status, changed_at, changed_by FROM status_history "
                "WHERE ref_id = ? ORDER BY id",
                (ref_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def closure_requests(self, ref_id: Optional[str] = None, pending_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM closure_approvals"
        clauses, params = [], []
        if ref_id:
            clauses.append("ref_id = ?")
            params.append(ref_id)
        if pending_only:
            clauses.append("decision = 'PENDING'")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        with self.reader() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def status_counts(self) -> Dict[str, int]:
        counts = {s.value: 0 for s in Status}
        with self.reader() as conn:
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM reports GROUP BY status"):
                counts[row["status"]] = int(row["n"])
        return counts

    def audit_entries(self, ref_id: Optional[str] = None, limit: Optional[int] = None) -> List[AuditEntry]:
        with self.reader() as conn:
            return audit.list_entries(conn, ref_id=ref_id, limit=limit)

    def verify_audit_chain(self) -> ChainReport:
        with self.reader() as conn:
            return audit.verify_chain(conn)

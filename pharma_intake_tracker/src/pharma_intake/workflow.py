"""Status lifecycle (New -> In Review -> Closed) with a Human-in-the-Loop closure gate.

In Review -> Closed never happens directly. It opens a closure request and
freezes the report. Only a designated human reviewer, with an explicit
attestation, can approve it. The approval is written to the audit trail in
the same transaction, before the status changes. Database triggers enforce the
same rules, so a direct SQL UPDATE cannot skip the gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from . import config
from .audit import AuditAction
from .errors import ApprovalError, ApprovalPending, InvalidTransition, ReportNotFound
from .repository import Repository
from .schemas import Status

TRANSITIONS: Dict[Status, Tuple[Status, ...]] = {
    Status.NEW: (Status.IN_REVIEW,),
    Status.IN_REVIEW: (Status.CLOSED,),
    Status.CLOSED: (),
}

REJECTION_REASONS = ("INCOMPLETE_INFORMATION", "FOLLOW_UP_REQUIRED", "DATA_QUALITY", "OTHER")


@dataclass(frozen=True)
class TransitionOutcome:
    ref_id: str
    from_status: Status
    to_status: Status
    applied: bool
    pending_approval: bool
    message: str


def allowed_next(status: Union[Status, str]) -> Tuple[Status, ...]:
    return TRANSITIONS[Status(status)]


class WorkflowService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _locked_state(conn: Any, ref_id: str) -> Tuple[Status, bool]:
        row = conn.execute(
            "SELECT status, pending_closure FROM reports WHERE ref_id = ?", (ref_id,)
        ).fetchone()
        if row is None:
            raise ReportNotFound(f"No report found with reference {ref_id}")
        return Status(row["status"]), bool(row["pending_closure"])

    @staticmethod
    def _check_transition(ref_id: str, current: Status, pending: bool, target: Status) -> None:
        if pending:
            raise ApprovalPending(
                f"{ref_id} is frozen: a closure approval is pending. A reviewer must approve or reject it first."
            )
        if target not in TRANSITIONS[current]:
            nxt = ", ".join(s.value for s in TRANSITIONS[current]) or "none (Closed is final)"
            raise InvalidTransition(
                f"{ref_id} cannot move from '{current.value}' to '{target.value}'. Allowed next status: {nxt}."
            )

    def _history(self, conn: Any, ref_id: str, src: Status, dst: Status, actor: str) -> None:
        conn.execute(
            "INSERT INTO status_history(ref_id, from_status, to_status, changed_at, changed_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (ref_id, src.value, dst.value, self.repo.now_iso(), actor),
        )

    # ------------------------------------------------------------ transitions
    def advance(self, ref_id: str, to_status: Union[Status, str], actor: str, via: str = "ui") -> TransitionOutcome:
        """Request a status change. Moving to Closed opens the HITL gate instead of closing."""
        target = Status(to_status)
        if target is Status.CLOSED:
            return self.request_closure(ref_id, actor, via=via)
        with self.repo.transaction() as conn:
            current, pending = self._locked_state(conn, ref_id)
            self._check_transition(ref_id, current, pending, target)
            conn.execute(
                "UPDATE reports SET status = ?, updated_at = ? WHERE ref_id = ?",
                (target.value, self.repo.now_iso(), ref_id),
            )
            self._history(conn, ref_id, current, target, actor)
            self.repo.write_audit(
                conn,
                actor,
                AuditAction.STATUS_CHANGED,
                ref_id,
                {"from_status": current.value, "to_status": target.value, "via": via},
            )
        return TransitionOutcome(ref_id, current, target, True, False, f"{ref_id} moved to {target.value}.")

    def request_closure(self, ref_id: str, actor: str, via: str = "ui") -> TransitionOutcome:
        with self.repo.transaction() as conn:
            current, pending = self._locked_state(conn, ref_id)
            self._check_transition(ref_id, current, pending, Status.CLOSED)
            conn.execute(
                "INSERT INTO closure_approvals(ref_id, requested_at, requested_by) VALUES (?, ?, ?)",
                (ref_id, self.repo.now_iso(), actor),
            )
            conn.execute(
                "UPDATE reports SET pending_closure = 1, updated_at = ? WHERE ref_id = ?",
                (self.repo.now_iso(), ref_id),
            )
            self.repo.write_audit(
                conn,
                actor,
                AuditAction.CLOSURE_REQUESTED,
                ref_id,
                {"from_status": current.value, "to_status": Status.CLOSED.value, "decision": "PENDING", "via": via},
            )
        return TransitionOutcome(
            ref_id,
            current,
            Status.CLOSED,
            False,
            True,
            f"Closure of {ref_id} requested. The report is frozen until a human reviewer approves it.",
        )

    # ------------------------------------------------------------ HITL decisions (humans only)
    @staticmethod
    def _require_reviewer(reviewer_id: str) -> None:
        if reviewer_id not in config.REVIEWERS:
            raise ApprovalError(
                "Only a designated human reviewer (REV-xx) can decide a closure request; operators and the agent cannot."
            )

    def _pending_request(self, conn: Any, ref_id: str) -> Dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM closure_approvals WHERE ref_id = ? AND decision = 'PENDING'", (ref_id,)
        ).fetchone()
        if row is None:
            raise ApprovalError(f"{ref_id} has no pending closure request.")
        return dict(row)

    def approve_closure(self, ref_id: str, reviewer_id: str, attestation: bool) -> TransitionOutcome:
        self._require_reviewer(reviewer_id)
        if attestation is not True:
            raise ApprovalError(
                "Approval requires the reviewer attestation: the record is complete and has been reviewed "
                "within this tool's non-clinical scope."
            )
        now = self.repo.now_iso()
        with self.repo.transaction() as conn:
            request = self._pending_request(conn, ref_id)
            conn.execute(
                "UPDATE closure_approvals SET decision = 'APPROVED', decided_at = ?, decided_by = ?, attestation = 1 "
                "WHERE id = ?",
                (now, reviewer_id, request["id"]),
            )
            # Authorization is recorded in the audit trail BEFORE the state unfreezes.
            self.repo.write_audit(
                conn, reviewer_id, AuditAction.CLOSURE_APPROVED, ref_id, {"decision": "APPROVED", "attestation": True}
            )
            conn.execute(
                "UPDATE reports SET status = 'Closed', pending_closure = 0, updated_at = ? WHERE ref_id = ?",
                (now, ref_id),
            )
            self._history(conn, ref_id, Status.IN_REVIEW, Status.CLOSED, reviewer_id)
            self.repo.write_audit(
                conn,
                reviewer_id,
                AuditAction.STATUS_CHANGED,
                ref_id,
                {"from_status": Status.IN_REVIEW.value, "to_status": Status.CLOSED.value, "via": "hitl"},
            )
        return TransitionOutcome(
            ref_id, Status.IN_REVIEW, Status.CLOSED, True, False, f"{ref_id} closed after approval by {reviewer_id}."
        )

    def reject_closure(self, ref_id: str, reviewer_id: str, reason: str = "OTHER") -> TransitionOutcome:
        self._require_reviewer(reviewer_id)
        if reason not in REJECTION_REASONS:
            raise ApprovalError(f"Rejection reason must be one of: {', '.join(REJECTION_REASONS)}.")
        now = self.repo.now_iso()
        with self.repo.transaction() as conn:
            request = self._pending_request(conn, ref_id)
            conn.execute(
                "UPDATE closure_approvals SET decision = 'REJECTED', decided_at = ?, decided_by = ?, reason = ? "
                "WHERE id = ?",
                (now, reviewer_id, reason, request["id"]),
            )
            conn.execute(
                "UPDATE reports SET pending_closure = 0, updated_at = ? WHERE ref_id = ?", (now, ref_id)
            )
            self.repo.write_audit(
                conn, reviewer_id, AuditAction.CLOSURE_REJECTED, ref_id, {"decision": "REJECTED", "reason": reason}
            )
        return TransitionOutcome(
            ref_id,
            Status.IN_REVIEW,
            Status.IN_REVIEW,
            False,
            False,
            f"Closure of {ref_id} rejected by {reviewer_id} ({reason}); the report stays In Review.",
        )

    def pending_approvals(self) -> List[Dict[str, Any]]:
        return self.repo.closure_requests(pending_only=True)

    def next_actions(self, ref_id: str) -> Tuple[Status, ...]:
        report = self.repo.get_report(ref_id)
        return () if report.pending_closure else allowed_next(report.status)

    def describe(self, ref_id: str) -> Dict[str, Optional[Any]]:
        report = self.repo.get_report(ref_id)
        return {
            "ref_id": report.ref_id,
            "status": report.status.value,
            "pending_closure": report.pending_closure,
            "allowed_next": [s.value for s in self.next_actions(ref_id)],
        }

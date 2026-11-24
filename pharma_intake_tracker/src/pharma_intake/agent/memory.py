"""Dual-memory engine (control plane, step 2).

Short-term memory: a volatile, bounded ReAct trace (Thought -> Action ->
Observation) for the current chat session. It lives in process memory only
(Streamlit session_state) and is never written to disk.

Long-term memory: the SQLite Repository (reports, status history, closure
approvals, audit trail). ``LongTermMemory`` is a read-only facade for the agent.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from .. import config
from ..repository import Repository

STEP_KINDS = ("thought", "action", "observation")


@dataclass(frozen=True)
class TraceStep:
    turn: int
    step: int
    kind: str
    content: str
    ts: str


class ShortTermMemory:
    def __init__(self, limit: int = config.SHORT_TERM_MEMORY_LIMIT) -> None:
        if limit <= 0:
            raise ValueError("memory limit must be positive")
        self.limit = limit
        self._steps: Deque[TraceStep] = deque(maxlen=limit)
        self._turn = 0
        self._step = 0

    def new_turn(self) -> int:
        self._turn += 1
        self._step = 0
        return self._turn

    def _add(self, kind: str, content: str) -> TraceStep:
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown ReAct step kind: {kind}")
        self._step += 1
        step = TraceStep(self._turn, self._step, kind, content,
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._steps.append(step)
        return step

    def thought(self, content: str) -> TraceStep:
        return self._add("thought", content)

    def action(self, content: str) -> TraceStep:
        return self._add("action", content)

    def observation(self, content: str) -> TraceStep:
        return self._add("observation", content)

    def steps(self, turn: Optional[int] = None) -> List[TraceStep]:
        return [s for s in self._steps if turn is None or s.turn == turn]

    @property
    def current_turn(self) -> int:
        return self._turn

    def clear(self) -> None:
        self._steps.clear()
        self._turn = 0
        self._step = 0

    def __len__(self) -> int:
        return len(self._steps)


class LongTermMemory:
    """Read-only facade over the persistent store, as seen by the agent."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def report_summary(self, ref_id: str) -> Dict[str, Any]:
        report = self._repo.get_report(ref_id)
        return {
            "ref_id": report.ref_id,
            "report_type": report.report_type.value,
            "product_name": report.product_name,
            "batch_lot_number": report.batch_lot_number,
            "status": report.status.value,
            "pending_closure": report.pending_closure,
            "date_received": report.date_received.isoformat(),
        }

    def status_history(self, ref_id: str) -> List[Dict[str, Any]]:
        return self._repo.status_history(ref_id)

    def audit_actions(self, ref_id: str) -> List[str]:
        return [e.action for e in self._repo.audit_entries(ref_id=ref_id)]

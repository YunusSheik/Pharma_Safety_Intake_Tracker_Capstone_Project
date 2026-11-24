"""Typed errors. Messages never echo free-text field values."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


class PharmaIntakeError(Exception):
    """Base class for all domain errors."""


@dataclass(frozen=True)
class FieldIssue:
    field: str
    message: str
    code: str


class IntakeValidationError(PharmaIntakeError):
    def __init__(self, issues: Sequence[FieldIssue]):
        self.issues: List[FieldIssue] = list(issues)
        super().__init__("; ".join(f"{i.field}: {i.message}" for i in self.issues))

    @property
    def fields(self) -> List[str]:
        return sorted({i.field for i in self.issues})


class ReportNotFound(PharmaIntakeError):
    pass


class ReferenceError_(PharmaIntakeError):
    """Reference reservation problems (unknown, already used, exhausted)."""


class InvalidTransition(PharmaIntakeError):
    pass


class ApprovalPending(PharmaIntakeError):
    """The report is frozen by a pending HITL closure approval."""


class ApprovalError(PharmaIntakeError):
    """A HITL decision was attempted without the required authority or attestation."""


class AuditDetailError(PharmaIntakeError):
    """An audit entry tried to store a non-whitelisted or free-text detail."""


class AuditIntegrityError(PharmaIntakeError):
    pass


class CircuitOpenError(PharmaIntakeError):
    """The agent circuit breaker is open; tool execution is halted until reset."""


class OfflineViolation(PharmaIntakeError):
    """An attempt to reach a non-loopback network address."""


class ModelUnavailable(PharmaIntakeError):
    """Real RAG models were required (PHARMA_RAG_MODE=real) but are not available locally."""

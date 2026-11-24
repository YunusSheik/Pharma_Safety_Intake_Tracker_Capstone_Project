"""Type-safe agent tools (control plane, step 1) and the executor that runs them.

Every tool call goes through these steps:
  circuit breaker check -> safe auto-repair (keys + formats) ->
  Pydantic validation (strict ASCII regexes) -> domain call -> structured result

Auto-repair only rewrites unambiguous formatting (case, separators,
zero-padding, known key aliases or near-miss key typos) and records each
repair. Anything else becomes a structured ToolError, never a traceback.
Repeated identical invalid calls trip the circuit breaker (agent/breaker.py).

There is deliberately NO tool that approves a closure: the HITL decision
exists only in the UI, for human reviewers.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config
from .agent.breaker import CircuitBreaker, call_signature
from .audit import AuditAction, query_fingerprint
from .errors import (
    ApprovalError,
    ApprovalPending,
    IntakeValidationError,
    InvalidTransition,
    ReferenceError_,
    ReportNotFound,
)
from .repository import Repository
from .schemas import FIELD_LABELS, FREE_TEXT_FIELDS, INTAKE_FIELDS, Status, validate_intake
from .workflow import WorkflowService, allowed_next

_REF_RE = re.compile(config.REF_PATTERN, re.ASCII)


# ---------------------------------------------------------------- argument schemas
def _check_ref(value: str) -> str:
    if not _REF_RE.fullmatch(value):
        raise ValueError("ref_id must match REF-YYYY-NNNN (e.g. REF-2026-0007)")
    return value


class UpdateStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ref_id: str
    new_status: Status

    @field_validator("ref_id")
    @classmethod
    def _ref_format(cls, value: str) -> str:
        return _check_ref(value)


class GetReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ref_id: str

    @field_validator("ref_id")
    @classmethod
    def _ref_format(cls, value: str) -> str:
        return _check_ref(value)


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=500)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fields: Tuple[str, ...]
    description: str
    args_model: Optional[Type[BaseModel]] = None   # None => validated by schemas.validate_intake


TOOLS: Dict[str, ToolSpec] = {
    "submit_intake_report": ToolSpec(
        "submit_intake_report", INTAKE_FIELDS, "Validate and save a new fictional intake report."
    ),
    "update_status": ToolSpec(
        "update_status", ("ref_id", "new_status"),
        "Move a report New -> In Review, or request closure (In Review -> Closed opens the HITL gate).",
        UpdateStatusArgs,
    ),
    "get_report": ToolSpec("get_report", ("ref_id",), "Look up a report's status and allowed next step.", GetReportArgs),
    "search_knowledge": ToolSpec(
        "search_knowledge", ("query",), "4-stage hybrid RAG over SOPs and past reports.", SearchKnowledgeArgs
    ),
}

# ---------------------------------------------------------------- auto-repair
KEY_ALIASES = {
    "type": "report_type", "category": "report_type", "report_category": "report_type",
    "product": "product_name", "drug": "product_name", "medicine": "product_name",
    "lot": "batch_lot_number", "batch": "batch_lot_number", "batch_id": "batch_lot_number",
    "lot_number": "batch_lot_number", "batch_number": "batch_lot_number", "lot_no": "batch_lot_number",
    "received": "date_received", "received_date": "date_received", "date": "date_received",
    "reporter": "reporter_type", "channel": "intake_channel", "source": "intake_channel",
    "desc": "description", "details": "description", "summary": "description",
    "event": "event_description", "onset": "event_onset_date", "onset_date": "event_onset_date",
    "status": "new_status", "to": "new_status", "target_status": "new_status", "to_status": "new_status",
    "ref": "ref_id", "reference": "ref_id", "id": "ref_id", "reference_id": "ref_id",
    "q": "query", "question": "query", "text": "query",
}

_STATUS_WORDS = {
    "new": "New", "inreview": "In Review", "review": "In Review", "underreview": "In Review",
    "reviewing": "In Review", "closed": "Closed", "close": "Closed",
}
_TYPE_WORDS = {
    "ae": "Suspected Adverse Event", "adverseevent": "Suspected Adverse Event",
    "suspectedadverseevent": "Suspected Adverse Event", "adverse": "Suspected Adverse Event",
    "pqc": "Product Quality Complaint", "qualitycomplaint": "Product Quality Complaint",
    "productqualitycomplaint": "Product Quality Complaint", "complaint": "Product Quality Complaint",
    "quality": "Product Quality Complaint",
}
_REPORTER_WORDS = {
    "hcp": "Healthcare Professional", "healthcareprofessional": "Healthcare Professional",
    "doctor": "Healthcare Professional", "physician": "Healthcare Professional", "nurse": "Healthcare Professional",
    "pharmacist": "Pharmacist", "consumer": "Consumer", "patient": "Consumer",
    "salesrep": "Sales Representative", "salesrepresentative": "Sales Representative", "rep": "Sales Representative",
    "other": "Other",
}
_CHANNEL_WORDS = {"email": "Email", "mail": "Email", "phone": "Telephone", "telephone": "Telephone",
                  "call": "Telephone", "tel": "Telephone"}

_LOT_REPAIR = re.compile(r"^\s*(?:lot[\s_\-:#.]*)?([0-9]{5})\s*$", re.I | re.ASCII)
_REF_REPAIR = re.compile(r"^\s*ref[\s_\-:#]*([0-9]{4})[\s_\-/]*([0-9]{1,4})\s*$", re.I | re.ASCII)
_DATE_REPAIR = re.compile(r"^\s*([0-9]{4})[/.]([0-9]{1,2})[/.]([0-9]{1,2})\s*$", re.ASCII)


def _squash(value: str) -> str:
    return re.sub(r"[\s_\-]", "", value.lower())


def _repair_value(key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if key == "batch_lot_number":
        m = _LOT_REPAIR.match(value)
        return f"LOT-{m.group(1)}" if m else value
    if key == "ref_id":
        m = _REF_REPAIR.match(value)
        return f"REF-{m.group(1)}-{int(m.group(2)):04d}" if m else value
    if key == "new_status":
        return _STATUS_WORDS.get(_squash(value), value)
    if key == "report_type":
        return _TYPE_WORDS.get(_squash(value), value)
    if key == "reporter_type":
        return _REPORTER_WORDS.get(_squash(value), value)
    if key == "intake_channel":
        return _CHANNEL_WORDS.get(_squash(value), value)
    if key in {"date_received", "event_onset_date"}:
        m = _DATE_REPAIR.match(value)
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else value
    return value


def _display(key: str, value: Any) -> str:
    if key in FREE_TEXT_FIELDS and isinstance(value, str):
        return f"<{len(value)} chars>"
    return repr(value)


def auto_repair(tool_name: str, raw_args: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (repaired_args, human-readable list of repairs applied)."""
    spec = TOOLS[tool_name]
    repaired: Dict[str, Any] = {}
    repairs: List[str] = []
    for raw_key, value in raw_args.items():
        key = re.sub(r"[\s\-]+", "_", str(raw_key).strip().lower())
        if key not in spec.fields:
            alias = KEY_ALIASES.get(key)
            if alias in spec.fields:
                key = alias
            else:
                close = difflib.get_close_matches(key, spec.fields, n=1, cutoff=0.8)
                if close:
                    key = close[0]
        if key != raw_key:
            repairs.append(f"argument name '{raw_key}' -> '{key}'")
        new_value = _repair_value(key, value)
        if new_value != value:
            repairs.append(f"{key}: {_display(key, value)} -> {_display(key, new_value)}")
        if key in repaired:
            repairs.append(f"duplicate argument '{key}' ignored")
            continue
        repaired[key] = new_value
    return repaired, repairs


# ---------------------------------------------------------------- results
@dataclass
class ToolError:
    code: str                    # validation_error | unknown_tool | domain_error | circuit_open
    message: str
    fields: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[ToolError] = None
    repairs: List[str] = field(default_factory=list)
    halted: bool = False
    attempts: int = 0


def summarize_args(args: Mapping[str, Any]) -> str:
    """Args for the ReAct trace with free-text values masked (data minimization)."""
    return ", ".join(f"{k}={_display(k, v)}" for k, v in args.items())


class ToolExecutor:
    def __init__(
        self,
        repo: Repository,
        workflow: WorkflowService,
        pipeline: Any,
        actor: str,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.repo = repo
        self.workflow = workflow
        self.pipeline = pipeline
        self.actor = actor
        self.breaker = breaker or CircuitBreaker()
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "submit_intake_report": self._submit,
            "update_status": self._update_status,
            "get_report": self._get_report,
            "search_knowledge": self._search,
        }

    # ------------------------------------------------------------ public
    def call(self, tool_name: str, raw_args: Mapping[str, Any]) -> ToolResult:
        if self.breaker.is_open:
            return ToolResult(tool_name, False, error=ToolError(
                "circuit_open",
                "Circuit breaker is open; tool execution is halted until a human resets the agent."), halted=True)
        signature = call_signature(tool_name, raw_args)
        if tool_name not in TOOLS:
            return self._fail(tool_name, signature, ToolError(
                "unknown_tool", f"Unknown tool '{tool_name}'. Available: {', '.join(TOOLS)}."), [])
        args, repairs = auto_repair(tool_name, raw_args)
        ref = args.get("ref_id")
        ref_id = ref if isinstance(ref, str) and _REF_RE.fullmatch(ref) else None
        try:
            data = self._handlers[tool_name](args)
        except IntakeValidationError as exc:
            return self._fail(tool_name, signature, ToolError(
                "validation_error", f"{len(exc.issues)} field(s) need attention.",
                [{"field": i.field, "message": i.message} for i in exc.issues]), repairs, ref_id)
        except ValidationError as exc:
            return self._fail(tool_name, signature, ToolError(
                "validation_error", f"{exc.error_count()} argument(s) invalid.",
                [{"field": ".".join(str(p) for p in e["loc"]) or "args",
                  "message": e["msg"].removeprefix("Value error, ")}
                 for e in exc.errors(include_url=False, include_input=False)]), repairs, ref_id)
        except (InvalidTransition, ApprovalPending, ApprovalError, ReportNotFound, ReferenceError_) as exc:
            return self._fail(tool_name, signature, ToolError("domain_error", str(exc)), repairs, ref_id)
        self.breaker.record_success()
        return ToolResult(tool_name, True, data=data, repairs=repairs)

    def reset_breaker(self) -> None:
        self.breaker.reset()
        self.repo.log_event(self.actor, AuditAction.CIRCUIT_BREAKER_RESET, details={"via": "agent"})

    # ------------------------------------------------------------ failure path
    def _fail(self, tool: str, signature: str, error: ToolError, repairs: List[str],
              ref_id: Optional[str] = None) -> ToolResult:
        state = self.breaker.record_failure(signature)
        fields = sorted({f["field"].split(".")[0] for f in error.fields if f.get("field")})
        self.repo.log_event(self.actor, AuditAction.TOOL_CALL_FAILED, ref_id=ref_id, details={
            "tool": tool if tool in TOOLS else "unknown", "reason": error.code, "fields": fields,
            "error_count": max(1, len(error.fields)), "attempts": state.consecutive_invalid, "via": "agent"})
        if state.tripped_now:
            self.repo.log_event(self.actor, AuditAction.CIRCUIT_BREAKER_TRIPPED, details={
                "tool": tool if tool in TOOLS else "unknown", "signature": signature[:16],
                "attempts": state.consecutive_invalid, "via": "agent"})
        return ToolResult(tool, False, error=error, repairs=repairs, halted=state.tripped_now,
                          attempts=state.consecutive_invalid)

    # ------------------------------------------------------------ handlers
    def _submit(self, args: Dict[str, Any]) -> Dict[str, Any]:
        report = validate_intake(args, today=self.repo.today())     # Pydantic, all issues at once
        ref_id = self.repo.reserve_reference(self.actor, via="agent")
        stored = self.repo.create_report(ref_id, report, self.actor, via="agent")
        return {"ref_id": stored.ref_id, "status": stored.status.value, "report_type": stored.report_type.value}

    def _update_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        parsed = UpdateStatusArgs.model_validate(args)
        outcome = self.workflow.advance(parsed.ref_id, parsed.new_status, self.actor, via="agent")
        return {"ref_id": outcome.ref_id, "from_status": outcome.from_status.value,
                "to_status": outcome.to_status.value, "applied": outcome.applied,
                "pending_approval": outcome.pending_approval, "message": outcome.message}

    def _get_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        parsed = GetReportArgs.model_validate(args)
        report = self.repo.get_report(parsed.ref_id)
        nxt = [] if report.pending_closure else [s.value for s in allowed_next(report.status)]
        return {"ref_id": report.ref_id, "report_type": report.report_type.value,
                "product_name": report.product_name, "batch_lot_number": report.batch_lot_number,
                "status": report.status.value, "pending_closure": report.pending_closure, "allowed_next": nxt}

    def _search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        parsed = SearchKnowledgeArgs.model_validate(args)
        self.pipeline.index(self.repo.list_reports())       # embeddings are cached; cheap
        rag = self.pipeline.answer(parsed.query)
        details = dict(query_fingerprint(parsed.query))
        details.update({"result_count": len(rag.retrieval.final),
                        "stage_counts": rag.retrieval.stage_counts,
                        "mode": "real" if self.pipeline.uses_real_models else "fallback", "via": "agent"})
        self.repo.log_event(self.actor, AuditAction.RAG_QUERY, details=details)
        return {"rag": rag, "result_count": len(rag.retrieval.final)}


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name, name)

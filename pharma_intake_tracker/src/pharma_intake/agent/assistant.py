"""The knowledge assistant: a deterministic ReAct-style orchestrator.

One turn:
  input guardrail -> breaker check -> deterministic router ->
  [parse arguments -> validated tool call (auto-repair, breaker)] -> observation -> reply

No LLM decides which tool to run. The router and the parsers are
deterministic, so the same message always produces the same actions. A local
LLM, if enabled, is only used to phrase the RAG answer from reranked context,
and its output is guardrailed too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..audit import AuditAction
from ..repository import Repository
from ..schemas import FIELD_LABELS
from ..tools import ToolExecutor, ToolResult, summarize_args
from ..workflow import WorkflowService
from .breaker import CircuitBreaker
from .guardrails import check_input
from .memory import ShortTermMemory
from .router import REF_LENIENT, Route, RouteDecision, SemanticRouter

DIRECT_REPLY = (
    "Hello! I'm the intake knowledge assistant for this training prototype (fictional data only). I can:\n"
    "- look up SOP guidance and past reports, e.g. \"Which complaints involve LOT-88392?\"\n"
    "- submit a new report from key=value fields, e.g. \"Submit a new report: type=PQC; product=...; "
    "lot=LOT-12345; received=2026-11-20; reporter=Pharmacist; channel=Email; description=...\"\n"
    "- check or advance a report's status, e.g. \"Move REF-2026-0003 to In Review\"\n\n"
    "I never assess causality, recommend treatment or decide reportability, and closing a report "
    "always needs a human reviewer's approval."
)

INTAKE_HELP = (
    "To submit through chat, send the fields as key=value pairs separated by semicolons. Mandatory fields: "
    "type (AE or PQC), product, lot (LOT-12345), received (YYYY-MM-DD), reporter (e.g. Pharmacist), "
    "channel (Email/Telephone), description. Adverse events also need event and onset (YYYY-MM-DD). "
    "Or use the **New Intake** tab, which reserves a reference number and checks each field as you go."
)

HALTED_REPLY = (
    "The circuit breaker is open, so tool execution is halted to stop a retry loop. "
    "A human must reset the agent (sidebar → Reset agent) before I can run tools again."
)

_TARGET_EXPLICIT = re.compile(r"\b(?:to|as|into)\s+(new|in[\s\-]?review|under\s+review|review|closed?)\b", re.I)
_TARGET_CLOSE = re.compile(r"\b(?:close|closure|closed)\b", re.I)
_TARGET_REVIEW = re.compile(r"\b(?:start|begin|commence)\w*\s+(?:the\s+)?review\b|\breview\s+it\b", re.I)


def parse_fields(message: str) -> Dict[str, str]:
    """Extract key=value (or key: value) pairs separated by ';' or newlines."""
    fields: Dict[str, str] = {}
    for segment in re.split(r"[;\n]", message or ""):
        if "=" in segment:
            left, value = segment.split("=", 1)
            key = left.split(":")[-1]
        elif ":" in segment:
            key, value = segment.split(":", 1)
        else:
            continue
        key, value = key.strip(), value.strip().strip("\"'").strip()
        if key and value and len(key) <= 30 and re.fullmatch(r"[A-Za-z][A-Za-z _/\-]*", key):
            fields[key] = value
    return fields


def parse_target_status(message: str) -> Optional[str]:
    match = _TARGET_EXPLICIT.search(message or "")
    if match:
        return match.group(1)
    if _TARGET_CLOSE.search(message or ""):
        return "closed"
    if _TARGET_REVIEW.search(message or ""):
        return "in review"
    return None


@dataclass
class AssistantReply:
    text: str
    turn: int
    route: Optional[Route] = None
    decision: Optional[RouteDecision] = None
    tool_results: List[ToolResult] = field(default_factory=list)
    rag: Any = None
    blocked: bool = False
    guardrail_reason: Optional[str] = None
    halted: bool = False


class Assistant:
    def __init__(
        self,
        repo: Repository,
        workflow: WorkflowService,
        pipeline: Any,
        actor: str,
        memory: Optional[ShortTermMemory] = None,
        breaker: Optional[CircuitBreaker] = None,
        router: Optional[SemanticRouter] = None,
    ) -> None:
        self.repo = repo
        self.actor = actor
        self.memory = memory or ShortTermMemory()
        self.executor = ToolExecutor(repo, workflow, pipeline, actor, breaker)
        self.router = router or SemanticRouter(getattr(pipeline, "encoder", None))

    @property
    def breaker(self) -> CircuitBreaker:
        return self.executor.breaker

    def reset(self) -> None:
        self.executor.reset_breaker()
        self.memory.new_turn()
        self.memory.thought("A human reset the circuit breaker; tool execution is re-enabled.")

    # ------------------------------------------------------------ main loop
    def handle(self, message: str) -> AssistantReply:
        turn = self.memory.new_turn()
        self.memory.thought(f"Received a message ({len(message.strip())} chars). Applying the input guardrail first.")
        verdict = check_input(message)
        if not verdict.allowed:
            self.memory.observation(f"Input guardrail blocked the request (reason: {verdict.reason}). No tool will run.")
            self.repo.log_event(self.actor, AuditAction.GUARDRAIL_BLOCKED,
                                details={"reason": verdict.reason, "via": "agent"})
            return AssistantReply(verdict.message, turn, blocked=True, guardrail_reason=verdict.reason)

        if self.breaker.is_open:
            self.memory.observation("Circuit breaker is open; refusing to execute tools.")
            return AssistantReply(HALTED_REPLY, turn, halted=True)

        decision = self.router.route(message)
        score = f", score {decision.score}" if decision.score is not None else ""
        self.memory.thought(f"Router selected '{decision.route.value}' ({decision.tier} tier: {decision.rule}{score}).")

        if decision.route is Route.DIRECT:
            self.memory.observation("Direct response; no tool needed.")
            return AssistantReply(DIRECT_REPLY, turn, decision.route, decision)
        if decision.route is Route.INTAKE:
            return self._intake(message, turn, decision)
        if decision.route is Route.STATUS:
            return self._status(message, turn, decision)
        return self._knowledge(message, turn, decision)

    # ------------------------------------------------------------ routes
    def _run_tool(self, tool: str, args: Dict[str, Any]) -> ToolResult:
        self.memory.action(f"{tool}({summarize_args(args)})")
        result = self.executor.call(tool, args)
        if result.repairs:
            self.memory.observation("Auto-repaired arguments: " + "; ".join(result.repairs))
        if result.ok:
            self.memory.observation(f"{tool} succeeded.")
        elif result.error is not None:
            fields = ", ".join(f["field"] for f in result.error.fields) or "-"
            self.memory.observation(
                f"{tool} returned {result.error.code} (fields: {fields}); consecutive identical invalid calls: {result.attempts}."
            )
        if result.halted:
            self.memory.observation("Circuit breaker tripped — execution halted.")
        return result

    @staticmethod
    def _failure_text(result: ToolResult, lead: str) -> str:
        assert result.error is not None
        lines = [lead]
        if result.error.fields:
            lines += [f"- {FIELD_LABELS.get(f['field'], f['field'])}: {f['message']}" for f in result.error.fields]
        else:
            lines.append(f"- {result.error.message}")
        if result.halted:
            lines.append("")
            lines.append(
                f"⛔ Circuit breaker opened after {result.attempts} identical invalid attempts. "
                "I've stopped executing tools; a human must reset the agent (sidebar → Reset agent)."
            )
        return "\n".join(lines)

    def _intake(self, message: str, turn: int, decision: RouteDecision) -> AssistantReply:
        fields = parse_fields(message)
        if not fields:
            self.memory.observation("No key=value fields found; returning intake instructions instead of calling a tool.")
            return AssistantReply(INTAKE_HELP, turn, decision.route, decision)
        self.memory.thought(f"Parsed {len(fields)} field(s); calling submit_intake_report.")
        result = self._run_tool("submit_intake_report", fields)
        if result.ok:
            d = result.data
            text = (f"Saved **{d['ref_id']}** as a {d['report_type']} (status {d['status']}). "
                    "It now appears on the Dashboard.")
        else:
            text = self._failure_text(result, "I couldn't submit the report yet:")
            if not result.halted:
                text += "\n\n" + INTAKE_HELP
        return AssistantReply(text, turn, decision.route, decision, [result], halted=result.halted)

    def _status(self, message: str, turn: int, decision: RouteDecision) -> AssistantReply:
        ref_match = REF_LENIENT.search(message)
        if not ref_match:
            self.memory.observation("No reference found in the message; asking for one.")
            return AssistantReply("Please include the report reference, e.g. REF-2026-0003.", turn,
                                  decision.route, decision)
        raw_ref = ref_match.group(0)
        target = parse_target_status(message)
        if target is None:
            self.memory.thought("No target status requested; looking the report up.")
            result = self._run_tool("get_report", {"ref_id": raw_ref})
            if result.ok:
                d = result.data
                pending = " — closure pending reviewer approval (frozen)" if d["pending_closure"] else ""
                nxt = ", ".join(d["allowed_next"]) or "none"
                text = (f"**{d['ref_id']}**: {d['report_type']}, {d['product_name']}, {d['batch_lot_number']}. "
                        f"Status: **{d['status']}**{pending}. Allowed next status: {nxt}.")
            else:
                text = self._failure_text(result, "I couldn't look that report up:")
            return AssistantReply(text, turn, decision.route, decision, [result], halted=result.halted)

        self.memory.thought(f"Requested transition to '{target}'; calling update_status.")
        result = self._run_tool("update_status", {"ref_id": raw_ref, "new_status": target})
        if result.ok:
            text = result.data["message"]
            if result.data.get("pending_approval"):
                text += " A reviewer must decide it in **Triage Workflow → Closure approvals** (the agent cannot approve)."
        else:
            text = self._failure_text(result, "I couldn't change the status:")
        return AssistantReply(text, turn, decision.route, decision, [result], halted=result.halted)

    def _knowledge(self, message: str, turn: int, decision: RouteDecision) -> AssistantReply:
        result = self._run_tool("search_knowledge", {"query": message})
        if not result.ok:
            return AssistantReply(self._failure_text(result, "I couldn't run that search:"), turn,
                                  decision.route, decision, [result], halted=result.halted)
        rag = result.data["rag"]
        r = rag.retrieval
        self.memory.observation(
            f"Retrieval: BM25 {len(r.bm25)} → dense {len(r.dense)} → RRF pool {len(r.fused)} → "
            f"{len(r.final)} chunk(s) ≥ threshold passed to the generator ({rag.answer.backend})."
        )
        if rag.output_blocked:
            self.memory.observation(f"Output guardrail withheld the answer (reason: {rag.blocked_reason}).")
            self.repo.log_event(self.actor, AuditAction.GUARDRAIL_BLOCKED,
                                details={"reason": rag.blocked_reason, "via": "agent", "route": "output"})
        text = rag.answer.text
        if rag.answer.citations:
            text += "\n\n**Sources:** " + "; ".join(rag.answer.citations)
        if rag.answer.note:
            text += f"\n\n_{rag.answer.note}_"
        return AssistantReply(text, turn, decision.route, decision, [result], rag=rag)

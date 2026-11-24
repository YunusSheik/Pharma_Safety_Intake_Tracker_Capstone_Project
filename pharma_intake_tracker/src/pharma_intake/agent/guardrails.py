"""Non-clinical guardrails (control plane, step 5).

Input guardrail: refuses requests to assess causality, recommend treatment
or dosing, or decide reportability or seriousness. It runs BEFORE routing,
so these requests never reach a tool, retrieval or a model.
Output guardrail: blocks generated text that states a clinical or regulatory
conclusion, even if it was quoted from context or produced by a local LLM.

Pure regular expressions: deterministic and testable, with no model judgement involved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Pattern, Sequence, Tuple

_REF = r"REF-\d{4}-\d{4}"

INPUT_RULES: Sequence[Tuple[str, Pattern[str]]] = (
    ("CAUSALITY", re.compile(
        r"\b(?:did|does|do|could|can|is|was|were|would|might|may)\b[^?.!]{0,80}?"
        r"\b(?:cause[sd]?|causing|responsible for|lead to|led to|trigger(?:ed|s)?|result(?:ed)? in)\b", re.I)),
    ("CAUSALITY", re.compile(
        r"\b(?:due to|because of|caused by|a side effect of|related to|linked to|attributable to)\s+"
        r"(?:the\s+|this\s+|that\s+)?(?:drug|product|medication|medicine|tablets?|capsules?|dose|vaccine)\b"
        r"(?!\s+(?:quality|complaints?|names?|labels?|leaflets?|packs?|packaging))", re.I)),
    ("CAUSALITY", re.compile(r"\bcausal(?:ity|ly)?\b[^?.!]{0,30}\b(?:of|between|for|link|relationship|related)\b", re.I)),
    ("CAUSALITY", re.compile(r"\b(?:assess|determine|evaluate|establish|judge)\w*\s+(?:the\s+)?causality\b", re.I)),
    ("CAUSALITY", re.compile(
        r"\b(?:is|was|are|were)\s+(?:(?:this|it|that|the)(?:\s+(?:event|reaction|rash|symptom|case))?|" + _REF + r")"
        r"\s+(?:related|linked|attributable|connected)\b", re.I)),
    ("TREATMENT", re.compile(
        r"\b(?:how|what|which|should|can|could)\b[^?.!]{0,80}?"
        r"\b(?:treat|treatment|therapy|antidote|remedy|medicate|prescribe)\w*\b", re.I)),
    ("TREATMENT", re.compile(
        r"\b(?:recommend|suggest|advise)\w*\b[^?.!]{0,40}\b(?:treatment|dose|dosage|medication|drug|therapy)\b", re.I)),
    ("TREATMENT", re.compile(
        r"\b(?:should|can|must)\s+(?:the\s+)?(?:patient|consumer|they|he|she|i|we)\s+"
        r"(?:stop|continue|discontinue|increase|decrease|reduce|double|skip)\b", re.I)),
    ("TREATMENT", re.compile(r"\b(?:what|which)\s+dose\b|\bdos(?:e|age)\s+(?:adjust|change|reduc|increas)", re.I)),
    ("REPORTABILITY", re.compile(
        r"\b(?:is|are|was|should|must|do|does|need)\b[^?.!]{0,80}?\b(?:reportable|be reported to|"
        r"report(?:ed)? (?:it |this )?to (?:the )?(?:fda|ema|mhra|regulator|authorit|health authorit|competent authorit))", re.I)),
    ("REPORTABILITY", re.compile(r"\b(?:determine|assess|decide|evaluate)\w*\s+(?:the\s+)?(?:reportability|seriousness|expectedness)\b", re.I)),
    ("REPORTABILITY", re.compile(
        r"\b(?:is|was)\s+(?:this|it|the (?:event|case|report)|" + _REF + r")\s+(?:serious|expedited|unexpected|a SUSAR)\b", re.I)),
    ("CLINICAL_DECISION", re.compile(r"\b(?:diagnos(?:e|is)|prognosis)\b", re.I)),
)

OUTPUT_RULES: Sequence[Tuple[str, Pattern[str]]] = (
    ("CAUSALITY", re.compile(r"\b(?:was|were|is|are|likely|probably|possibly|definitely)\s+(?:\w+\s+){0,2}caused by\b", re.I)),
    ("CAUSALITY", re.compile(r"\bcausally (?:related|associated|linked)\b|\bcausal (?:relationship|association) (?:is|was|exists)\b", re.I)),
    ("TREATMENT", re.compile(
        r"\b(?:recommend|advise|suggest)\w*\s+(?:that\s+)?(?:the\s+)?(?:patient|consumer|reporter|you)\s+"
        r"(?:take|stop|start|use|increase|decrease|discontinue)\b", re.I)),
    ("TREATMENT", re.compile(r"\bshould be treated with\b|\bthe (?:patient|consumer) should (?:take|stop)\b", re.I)),
    ("REPORTABILITY", re.compile(r"\b(?:is|are|was)\s+(?:not\s+)?(?:a\s+)?(?:reportable|expedited)\b", re.I)),
    ("REPORTABILITY", re.compile(r"\bmust be reported (?:to|within)\b", re.I)),
)

REFUSALS = {
    "CAUSALITY": (
        "I can't assess whether a product caused an event. Causality assessment is outside this tool's "
        "scope and is done by qualified pharmacovigilance personnel in the enterprise system. "
        "I can find related SOP guidance or past reports for you instead."
    ),
    "TREATMENT": (
        "I can't recommend or comment on medical treatment or dosing. Anyone needing medical help should "
        "contact a healthcare professional. I can help record the report or find SOP guidance."
    ),
    "REPORTABILITY": (
        "I can't determine regulatory reportability or seriousness. Those decisions are made by qualified "
        "personnel in the enterprise pharmacovigilance system. I can help capture the report completely "
        "and route it for review."
    ),
    "CLINICAL_DECISION": (
        "I can't make clinical judgements such as diagnoses. This training tool only records and tracks "
        "fictional intake reports and looks up SOP guidance."
    ),
}

BLOCKED_OUTPUT_MESSAGE = (
    "The generated answer was withheld because it appeared to state a clinical or regulatory conclusion, "
    "which this tool must never do. Please review the cited source excerpts directly."
)


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: Optional[str] = None
    message: str = ""


def _first_violation(text: str, rules: Sequence[Tuple[str, Pattern[str]]]) -> Optional[str]:
    for reason, pattern in rules:
        if pattern.search(text or ""):
            return reason
    return None


def check_input(text: str) -> GuardrailResult:
    reason = _first_violation(text, INPUT_RULES)
    if reason:
        return GuardrailResult(False, reason, REFUSALS[reason])
    return GuardrailResult(True)


def check_output(text: str) -> GuardrailResult:
    reason = _first_violation(text, OUTPUT_RULES)
    if reason:
        return GuardrailResult(False, reason, BLOCKED_OUTPUT_MESSAGE)
    return GuardrailResult(True)

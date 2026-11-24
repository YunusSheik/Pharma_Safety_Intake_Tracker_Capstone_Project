"""Deterministic semantic router (control plane, step 3).

Runs BEFORE any tool or LLM call and maps a message to exactly one route:
  1. Direct Response          — greetings, thanks, "what can you do"
  2. Hybrid RAG               — SOP / historical report lookups
  3. Intake Form Tool         — new submissions
  4. Status Workflow Manager  — triage status lookups and updates

Tier 1 is ordered regular-expression rules (fully explainable). Tier 2, only
for messages no rule matches, compares the message embedding with fixed
example phrases for each route against a fixed threshold. Tier 3 defaults to
Hybrid RAG, which can always answer "no relevant record found". The same
input always gives the same route, and there is no sampling or LLM call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

import numpy as np

from ..rag.dense import Encoder


class Route(str, Enum):
    DIRECT = "Direct Response"
    RAG = "Hybrid RAG"
    INTAKE = "Intake Form Tool"
    STATUS = "Status Workflow Manager"


REF_LENIENT = re.compile(r"\bref[\s_\-:#]*\d{4}[\s_\-/]*\d{1,4}\b", re.I)

RULES: Sequence[Tuple[str, Route, Pattern[str]]] = (
    ("greeting", Route.DIRECT, re.compile(
        r"^\W*(?:hi|hello|hey|hiya|greetings|good\s+(?:morning|afternoon|evening|day)|thanks|thank\s+you|"
        r"cheers|bye|goodbye)\b[\w\s,.!?'-]{0,40}$", re.I)),
    ("capabilities", Route.DIRECT, re.compile(
        r"\b(?:what can you do|what do you do|who are you|what are you|how do i use (?:this|you)|^help\W*$)", re.I)),
    ("reference_id", Route.STATUS, REF_LENIENT),
    ("new_submission", Route.INTAKE, re.compile(
        r"\b(?:new|submit|log|create|file|record|register|raise|enter|capture)\b[^.?!]{0,40}"
        r"\b(?:report|complaint|adverse\s+event|case|intake)\b", re.I)),
    ("field_list", Route.INTAKE, re.compile(r"(?:\b\w+\s*[:=][^;\n]+[;\n]\s*){2,}", re.I)),
    ("knowledge_lookup", Route.RAG, re.compile(
        r"\b(?:sop|sops|procedure|policy|guidance|lot-?\s?\d{5}|history|historical|previous|past|similar|"
        r"any\s+(?:reports?|complaints?|cases?)|how\s+(?:do|does|should|to|is|are)|what\s+(?:is|are|does|should|happens)|"
        r"which|when|why|who)\b", re.I)),
)

EXEMPLARS: Dict[Route, Tuple[str, ...]] = {
    Route.DIRECT: (
        "hello there", "good morning", "thanks for your help", "what can you help me with",
    ),
    Route.RAG: (
        "what does the procedure say about this", "find past reports mentioning a symptom",
        "look up complaints for a batch", "explain the closure approval rules",
    ),
    Route.INTAKE: (
        "I need to enter a new complaint", "a pharmacist called to report a problem with a product",
        "I want to submit an adverse event", "start a new intake form",
    ),
    Route.STATUS: (
        "move the case to in review", "what is the status of my report", "close the report",
        "advance the triage status",
    ),
}

SEMANTIC_THRESHOLDS = {"real": 0.72, "fallback": 0.40}


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    tier: str            # "rule" | "semantic" | "default"
    rule: str
    score: Optional[float] = None


class SemanticRouter:
    def __init__(self, encoder: Optional[Encoder] = None) -> None:
        self.encoder = encoder
        self._exemplar_routes: List[Route] = []
        self._exemplar_matrix: Optional[np.ndarray] = None
        if encoder is not None:
            texts: List[str] = []
            for route, phrases in EXEMPLARS.items():
                for phrase in phrases:
                    texts.append(phrase)
                    self._exemplar_routes.append(route)
            self._exemplar_matrix = encoder.encode(texts)

    @property
    def threshold(self) -> float:
        mode = getattr(self.encoder, "mode", "fallback")
        return SEMANTIC_THRESHOLDS.get(mode, SEMANTIC_THRESHOLDS["fallback"])

    def route(self, message: str) -> RouteDecision:
        text = (message or "").strip()
        if not text:
            return RouteDecision(Route.DIRECT, "rule", "empty")
        for name, route, pattern in RULES:
            if pattern.search(text):
                return RouteDecision(route, "rule", name)
        if self.encoder is not None and self._exemplar_matrix is not None:
            query = self.encoder.encode([text])[0]
            sims = self._exemplar_matrix @ query
            best = int(np.argmax(sims))      # argmax is deterministic: first max wins
            score = float(sims[best])
            if score >= self.threshold:
                return RouteDecision(self._exemplar_routes[best], "semantic", "exemplar_similarity", round(score, 4))
        return RouteDecision(Route.RAG, "default", "fallback_to_knowledge_lookup")

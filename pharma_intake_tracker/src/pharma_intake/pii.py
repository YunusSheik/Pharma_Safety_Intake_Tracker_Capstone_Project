"""Identifier detector for free-text fields (data-privacy shield).

This prototype accepts synthetic, de-identified narratives only. Any text that
looks like a direct or quasi-identifier is rejected at the schema boundary.
Hits report the identifier *kind* and position, never the matched value, so
error messages and logs cannot leak it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("national ID number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "medical record number",
        re.compile(r"\b(?:MRN|NHS\s*(?:no|number)|medical\s+record(?:\s+(?:no|number))?)\b", re.I),
    ),
    ("date of birth", re.compile(r"\b(?:DOB|D\.O\.B\.?|date\s+of\s+birth|born\s+on)\b", re.I)),
    (
        "person name",
        re.compile(
            r"\b(?:patient|pt|reporter|caller|consumer)(?:'s)?\s+(?:name\s*(?:is|:)|named|called)\b",
            re.I,
        ),
    ),
    ("person name", re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx)\.?\s+[A-Z][a-z]+")),
    (
        "street address",
        re.compile(
            r"\b\d{1,5}\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Boulevard|Blvd|Close|Way)\b"
        ),
    ),
    ("age", re.compile(r"\b\d{1,3}[- ](?:year|yr)s?[- ]old\b|\baged\s+\d{1,3}\b", re.I)),
)

# Phone numbers: long digit runs with separators. Counted separately so dates
# (8 digits), REF ids (8 digits) and lots (5 digits) do not trigger it.
_PHONE_CANDIDATE = re.compile(r"(?<![\w-])\+?\d[\d\s().-]{7,}\d(?![\w-])")
_PHONE_DIGITS = (10, 15)


@dataclass(frozen=True)
class IdentifierHit:
    kind: str
    start: int
    end: int


def find_identifiers(text: str) -> List[IdentifierHit]:
    """Return identifier-like spans in ``text`` (kinds only — never values)."""
    if not text:
        return []
    hits: List[IdentifierHit] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            hits.append(IdentifierHit(kind, match.start(), match.end()))
    for match in _PHONE_CANDIDATE.finditer(text):
        digits = sum(ch.isdigit() for ch in match.group(0))
        if _PHONE_DIGITS[0] <= digits <= _PHONE_DIGITS[1]:
            hits.append(IdentifierHit("phone number", match.start(), match.end()))
    hits.sort(key=lambda h: (h.start, h.kind))
    return hits


def identifier_kinds(text: str) -> List[str]:
    return sorted({hit.kind for hit in find_identifiers(text)})


def contains_identifiers(text: str) -> bool:
    return bool(find_identifiers(text))

"""Deterministic text utilities shared by BM25, the fallback encoder/reranker and the router."""
from __future__ import annotations

import re
from typing import List, Tuple

# Keeps codes like "lot-88392", "ref-2026-0001", "sop-003" and "0.5" as single tokens.
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")
_SPLIT_RE = re.compile(r"[-_./]")
# Sub-word spans used only for chunk sizing when no model tokenizer is loaded.
SPAN_RE = re.compile(r"\w+|[^\w\s]")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[\(])")

STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in into
    is it its me my no not of on or our should so than that the their them then there these this those
    to was we were what when where which who why will with would you your about any all also after
    before over under per via""".split()
)


def _stem(token: str) -> str:
    """Tiny plural/inflection stripper. Deterministic, alphabetic tokens only."""
    if not token.isalpha() or len(token) <= 4:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "sses", "xes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> List[str]:
    """Lowercased content tokens. Compound codes are kept whole AND split into parts."""
    out: List[str] = []
    for match in TOKEN_RE.finditer((text or "").lower()):
        token = match.group(0)
        if token in STOPWORDS:
            continue
        out.append(_stem(token))
        if _SPLIT_RE.search(token):
            out.extend(_stem(p) for p in _SPLIT_RE.split(token) if len(p) > 1 and p not in STOPWORDS)
    return out


def regex_spans(text: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in SPAN_RE.finditer(text or "")]


def split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in SENTENCE_RE.split((text or "").strip())]
    return [p for p in parts if p]


def bigrams(tokens: List[str]) -> List[Tuple[str, str]]:
    return list(zip(tokens, tokens[1:]))

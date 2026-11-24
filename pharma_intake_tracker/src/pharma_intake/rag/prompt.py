"""Grounded generation prompt built from the Stage 4 survivors only."""
from __future__ import annotations

from typing import Sequence

from .corpus import Chunk

SYSTEM_RULES = """You are the knowledge assistant of a TRAINING prototype that tracks FICTIONAL pharmaceutical
intake reports. Follow these rules without exception:
1. Answer ONLY from the numbered context below. Do not use outside knowledge.
2. Cite every statement with its source tag, e.g. [S1].
3. If the context does not contain the answer, reply exactly: "No sufficiently relevant record found."
4. NEVER assess causality, recommend or comment on treatment or dosing, determine reportability or
   seriousness, or make any clinical or regulatory decision.
5. Never invent reference numbers, lot numbers, dates or report details."""


def source_tag(index: int) -> str:
    return f"S{index}"


def build_prompt(query: str, chunks: Sequence[Chunk]) -> str:
    lines = [SYSTEM_RULES, "", "### Context"]
    if not chunks:
        lines.append("(no context passed the relevance threshold)")
    for i, chunk in enumerate(chunks, start=1):
        lines.append(f"[{source_tag(i)}] ({chunk.citation})")
        lines.append(chunk.text.strip())
        lines.append("")
    lines += ["### Question", query.strip(), "", "### Answer (cite sources as [S#])"]
    return "\n".join(lines)

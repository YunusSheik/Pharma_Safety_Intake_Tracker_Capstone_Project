"""Stage 3 — Reciprocal Rank Fusion.

RRF(d) = Σ_lists 1 / (k + rank_list(d)),  rank starting at 1, k = 60.

Only ranks are used, never raw scores, so BM25 scores (unbounded) and cosine
similarities ([-1, 1]) need no scale normalization. k = 60 flattens the
curve, so one list's #1 cannot dominate a document ranked well by both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .. import config


@dataclass
class FusedCandidate:
    item_id: str
    score: float
    ranks: Dict[str, int] = field(default_factory=dict)


def rrf_fuse(
    rankings: Dict[str, Sequence[str]],
    k: int = config.RRF_K,
    depth: int = max(config.BM25_TOP_K, config.DENSE_TOP_K),
) -> List[FusedCandidate]:
    """Fuse named ranked lists (best first). Each list is truncated to ``depth`` (spec: top-50)."""
    if k <= 0:
        raise ValueError("RRF k must be positive")
    pool: Dict[str, FusedCandidate] = {}
    for name, ranked in rankings.items():
        for rank, item_id in enumerate(list(ranked)[:depth], start=1):
            candidate = pool.setdefault(item_id, FusedCandidate(item_id, 0.0))
            candidate.score += 1.0 / (k + rank)
            candidate.ranks[name] = rank
    return sorted(pool.values(), key=lambda c: (-c.score, c.item_id))

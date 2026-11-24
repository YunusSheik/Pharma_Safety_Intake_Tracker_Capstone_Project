"""Stage 1 — Lexical sparse retrieval (Okapi BM25), implemented from first principles.

score(D, Q) = Σ_q IDF(q) · f(q,D)·(k1+1) / (f(q,D) + k1·(1 − b + b·|D|/avgdl))
IDF(q)      = ln(1 + (N − n(q) + 0.5) / (n(q) + 0.5))      (non-negative variant)

k1 sets term-frequency saturation (spec: 1.2–1.5). b = 0.75 normalizes
for document length, so long documents do not dilute exact matches on lot
numbers or term codes.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence, Tuple

from .. import config


class BM25Index:
    def __init__(
        self,
        documents: Sequence[Sequence[str]],
        k1: float = config.BM25_K1,
        b: float = config.BM25_B,
    ) -> None:
        low, high = config.BM25_K1_RANGE
        if not low <= k1 <= high:
            raise ValueError(f"BM25 k1 must be within [{low}, {high}] (spec), got {k1}")
        if not 0.0 <= b <= 1.0:
            raise ValueError("BM25 b must be within [0, 1]")
        self.k1 = k1
        self.b = b
        self.term_freqs: List[Counter] = [Counter(doc) for doc in documents]
        self.doc_lengths: List[int] = [len(doc) for doc in documents]
        self.n_docs = len(documents)
        self.avgdl = (sum(self.doc_lengths) / self.n_docs) if self.n_docs else 0.0
        df: Counter = Counter()
        for tf in self.term_freqs:
            df.update(tf.keys())
        self.idf: Dict[str, float] = {
            term: math.log(1.0 + (self.n_docs - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def score(self, query_tokens: Sequence[str], index: int) -> float:
        tf = self.term_freqs[index]
        length_norm = 1.0 - self.b + self.b * (self.doc_lengths[index] / self.avgdl if self.avgdl else 0.0)
        total = 0.0
        for term in query_tokens:
            freq = tf.get(term, 0)
            if not freq:
                continue
            total += self.idf.get(term, 0.0) * freq * (self.k1 + 1.0) / (freq + self.k1 * length_norm)
        return total

    def top_k(self, query_tokens: Sequence[str], k: int = config.BM25_TOP_K) -> List[Tuple[int, float]]:
        """Indices with a positive score, best first; ties broken by index (deterministic)."""
        unique_terms = list(dict.fromkeys(query_tokens))
        scored = [(i, self.score(unique_terms, i)) for i in range(self.n_docs)]
        positive = [(i, s) for i, s in scored if s > 0.0]
        positive.sort(key=lambda item: (-item[1], item[0]))
        return positive[:k]

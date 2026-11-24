"""The 4-stage hybrid RAG pipeline, with per-stage diagnostics.

query ─┬─> Stage 1 BM25 (top-50) ──┐
       └─> Stage 2 bi-encoder (top-50) ─┴─> Stage 3 RRF k=60 ─> Stage 4 cross-encoder
                                              (pool ≤ 100)        (≥ 0.40, top ≤ 5)
       ─> grounded prompt ─> generator ─> output guardrail ─> answer + citations
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import config
from ..agent.guardrails import check_output
from ..schemas import StoredReport
from .bm25 import BM25Index
from .corpus import Chunk, build_chunks
from .dense import DenseIndex, Encoder, load_encoder
from .generator import Answer, Generator, load_generator
from .prompt import build_prompt
from .rerank import Reranker, apply_threshold, load_reranker
from .rrf import rrf_fuse
from .text import tokenize


@dataclass(frozen=True)
class StageHit:
    chunk_id: str
    rank: int
    score: float
    detail: str = ""


@dataclass
class RetrievalResult:
    query: str
    bm25: List[StageHit]
    dense: List[StageHit]
    fused: List[StageHit]
    reranked: List[StageHit]
    final: List[Chunk]
    final_scores: List[float]
    mode: Dict[str, str]

    @property
    def stage_counts(self) -> Dict[str, int]:
        return {
            "bm25": len(self.bm25),
            "dense": len(self.dense),
            "fused": len(self.fused),
            "kept": len(self.final),
        }


@dataclass
class RAGAnswer:
    answer: Answer
    prompt: str
    retrieval: RetrievalResult
    output_blocked: bool = False
    blocked_reason: Optional[str] = None
    raw_answer: Optional[str] = field(default=None, repr=False)


class HybridRAGPipeline:
    def __init__(
        self,
        encoder: Encoder,
        reranker: Reranker,
        generator: Generator,
        sop_dir: Path = config.SOP_DIR,
        threshold: float = config.RERANK_THRESHOLD,
        top_k: int = config.RERANK_TOP_K,
    ) -> None:
        self.encoder = encoder
        self.reranker = reranker
        self.generator = generator
        self.sop_dir = Path(sop_dir)
        self.threshold = threshold
        self.top_k = top_k
        self.chunks: List[Chunk] = []
        self._by_id: Dict[str, Chunk] = {}
        self._bm25: Optional[BM25Index] = None
        self._dense: Optional[DenseIndex] = None
        self.index([])

    @property
    def mode(self) -> Dict[str, str]:
        return {
            "bi_encoder": f"{self.encoder.mode}:{self.encoder.name}",
            "cross_encoder": f"{self.reranker.mode}:{self.reranker.name}",
            "generator": self.generator.name,
            "chunk_tokens": self.encoder.token_counter.name,
        }

    @property
    def uses_real_models(self) -> bool:
        return self.encoder.mode == "real" and self.reranker.mode == "real"

    def index(self, reports: Sequence[StoredReport]) -> None:
        """(Re)build all indexes. Dense embeddings are cached by text, so re-indexing is cheap."""
        self.chunks = build_chunks(self.sop_dir, reports, self.encoder.token_counter)
        self._by_id = {c.chunk_id: c for c in self.chunks}
        self._bm25 = BM25Index([tokenize(c.text) for c in self.chunks])
        self._dense = DenseIndex(self.encoder, [c.text for c in self.chunks])

    def retrieve(self, query: str) -> RetrievalResult:
        assert self._bm25 is not None and self._dense is not None
        ids = [c.chunk_id for c in self.chunks]

        # Stage 1 — lexical
        bm25_hits = self._bm25.top_k(tokenize(query), config.BM25_TOP_K)
        bm25 = [StageHit(ids[i], r, s) for r, (i, s) in enumerate(bm25_hits, start=1)]
        # Stage 2 — semantic
        dense_hits = self._dense.search(query, config.DENSE_TOP_K)
        dense = [StageHit(ids[i], r, s) for r, (i, s) in enumerate(dense_hits, start=1)]
        # Stage 3 — RRF
        fused_candidates = rrf_fuse(
            {"bm25": [h.chunk_id for h in bm25], "dense": [h.chunk_id for h in dense]}, k=config.RRF_K
        )
        fused = [
            StageHit(c.item_id, r, c.score, ", ".join(f"{k}#{v}" for k, v in sorted(c.ranks.items())))
            for r, c in enumerate(fused_candidates, start=1)
        ]
        # Stage 4 — cross-encoder over the whole fused pool
        pool = [self._by_id[c.item_id] for c in fused_candidates]
        scores = self.reranker.score(query, [c.text for c in pool])
        decisions, kept = apply_threshold(scores, self.threshold, self.top_k)
        reranked = [
            StageHit(pool[d.index].chunk_id, r, d.score, "kept" if d.kept else "dropped")
            for r, d in enumerate(decisions, start=1)
        ]
        final = [pool[i] for i in kept]
        final_scores = [float(scores[i]) for i in kept]
        return RetrievalResult(query, bm25, dense, fused, reranked, final, final_scores, self.mode)

    def answer(self, query: str) -> RAGAnswer:
        retrieval = self.retrieve(query)
        prompt = build_prompt(query, retrieval.final)
        answer = self.generator.generate(query, retrieval.final, prompt)
        verdict = check_output(answer.text)
        if not verdict.allowed:
            raw = answer.text
            answer = Answer(verdict.message, answer.citations, answer.backend, "output guardrail")
            return RAGAnswer(answer, prompt, retrieval, True, verdict.reason, raw)
        return RAGAnswer(answer, prompt, retrieval)


def build_pipeline(
    reports: Sequence[StoredReport] = (),
    mode: Optional[str] = None,
    backend: Optional[str] = None,
    sop_dir: Path = config.SOP_DIR,
) -> HybridRAGPipeline:
    pipeline = HybridRAGPipeline(load_encoder(mode), load_reranker(mode), load_generator(backend), sop_dir)
    if reports:
        pipeline.index(reports)
    return pipeline

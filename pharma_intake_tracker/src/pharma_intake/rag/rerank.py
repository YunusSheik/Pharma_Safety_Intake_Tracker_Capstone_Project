"""Stage 4 — Precision cross-encoder reranking with a relevance threshold.

Real mode: mixedbread-ai/mxbai-rerank-xsmall-v1 (DeBERTa-v3 cross-encoder) reads
each (query, chunk) pair with full joint cross-attention. Its raw logit goes through a sigmoid, giving
a relevance probability in [0, 1]. Candidates below 0.40 are dropped, and
at most the top 5 (spec: 3–5) reach the generation prompt. If nothing clears
the threshold, nothing is passed on and the system says it found no answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Protocol, Sequence, Tuple

from .. import config
from ..errors import ModelUnavailable
from .dense import _force_offline, model_path, model_present
from .text import bigrams, tokenize


class Reranker(Protocol):
    name: str
    mode: str

    def score(self, query: str, texts: Sequence[str]) -> List[float]: ...


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class CrossEncoderReranker:
    mode = "real"

    def __init__(self, path) -> None:
        _force_offline()
        import torch  # deferred heavy imports
        from sentence_transformers import CrossEncoder

        self._identity = torch.nn.Identity()
        # float32: the checkpoint is stored in fp16, which is ~100x slower on CPU.
        self.model = CrossEncoder(
            str(path),
            device="cpu",
            max_length=config.CHUNK_TOKENS,
            local_files_only=True,
            model_kwargs={"dtype": torch.float32},
        )
        self.name = config.CROSS_ENCODER_MODEL

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        if not texts:
            return []
        logits = self.model.predict(
            [(query, t) for t in texts],
            activation_fn=self._identity,
            batch_size=16,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [sigmoid(float(v)) for v in logits]


class LexicalReranker:
    """Deterministic fallback in [0, 1]: query-term coverage plus phrase (bigram) coverage."""

    name = "lexical-fallback"
    mode = "fallback"

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        q_tokens = tokenize(query)
        q_terms = set(q_tokens)
        q_bigrams = set(bigrams(q_tokens))
        scores: List[float] = []
        for text in texts:
            t_tokens = tokenize(text)
            if not q_terms or not t_tokens:
                scores.append(0.0)
                continue
            t_terms = set(t_tokens)
            coverage = len(q_terms & t_terms) / len(q_terms)
            phrase = (len(q_bigrams & set(bigrams(t_tokens))) / len(q_bigrams)) if q_bigrams else coverage
            scores.append(round(min(1.0, 0.8 * coverage + 0.2 * phrase), 6))
        return scores


@lru_cache(maxsize=4)
def load_reranker(mode: Optional[str] = None) -> Reranker:
    mode = mode or config.rag_mode()
    if mode == "fallback":
        return LexicalReranker()
    if model_present(config.CROSS_ENCODER_DIRNAME):
        try:
            return CrossEncoderReranker(model_path(config.CROSS_ENCODER_DIRNAME))
        except Exception as exc:  # pragma: no cover - depends on local install
            if mode == "real":
                raise ModelUnavailable(f"cross-encoder failed to load: {type(exc).__name__}") from exc
            return LexicalReranker()
    if mode == "real":
        raise ModelUnavailable("cross-encoder not downloaded: run scripts/download_models.py")
    return LexicalReranker()


@dataclass(frozen=True)
class RerankDecision:
    index: int
    score: float
    kept: bool


def apply_threshold(
    scores: Sequence[float],
    threshold: float = config.RERANK_THRESHOLD,
    top_k: int = config.RERANK_TOP_K,
) -> Tuple[List[RerankDecision], List[int]]:
    """Sort by score (ties by input order), keep indices >= threshold, cap at top_k."""
    low, high = config.RERANK_TOP_K_RANGE
    if not low <= top_k <= high:
        raise ValueError(f"top_k must be within [{low}, {high}] (spec: top-3 to top-5)")
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    kept = [i for i in order if scores[i] >= threshold][:top_k]
    kept_set = set(kept)
    decisions = [RerankDecision(i, float(scores[i]), i in kept_set) for i in order]
    return decisions, kept

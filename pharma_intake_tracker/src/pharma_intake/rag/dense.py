"""Stage 2 — Dense semantic retrieval (bi-encoder + cosine similarity).

Real mode: BAAI/bge-small-en-v1.5 (384-d, 512-token max) loaded from the local
models/ directory, with HF_HUB_OFFLINE=1 so it never reaches the network.
Fallback mode: a deterministic hashed bag-of-features encoder. It keeps the
pipeline and the tests runnable without torch, but it captures lexical
overlap, not semantics. The UI and the diagnostics show which mode is active.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from .. import config
from ..errors import ModelUnavailable
from .chunker import HFTokenCounter, RegexTokenCounter, TokenCounter
from .text import tokenize


def _force_offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def model_path(dirname: str) -> Path:
    return config.models_dir() / dirname


def model_present(dirname: str) -> bool:
    path = model_path(dirname)
    return (path / "config.json").exists() and any(path.glob("*.safetensors"))


class Encoder(Protocol):
    name: str
    mode: str
    token_counter: TokenCounter

    def encode(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray: ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashingEncoder:
    """Deterministic fallback: signed feature hashing of word, bigram and char-trigram features."""

    name = "hashing-fallback"
    mode = "fallback"

    def __init__(self, dim: int = config.FALLBACK_EMBED_DIM) -> None:
        self.dim = dim
        self.token_counter: TokenCounter = RegexTokenCounter()

    def _features(self, text: str) -> List[Tuple[str, float]]:
        tokens = tokenize(text)
        feats: List[Tuple[str, float]] = [("w:" + t, 1.0) for t in tokens]
        feats += [("b:" + a + "_" + b, 0.7) for a, b in zip(tokens, tokens[1:])]
        for token in tokens:
            padded = f"<{token}>"
            feats += [("c:" + padded[i : i + 3], 0.3) for i in range(len(padded) - 2)]
        return feats

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature, weight in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            index = value % self.dim
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vec[index] += sign * weight
        return vec

    def encode(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _l2_normalize(np.vstack([self._vector(t) for t in texts]))


class SentenceTransformerEncoder:
    """Real bi-encoder, loaded strictly from local files, with an embedding cache keyed by text hash."""

    mode = "real"

    def __init__(self, path: Path) -> None:
        _force_offline()
        from sentence_transformers import SentenceTransformer  # heavy import, deferred

        self.model = SentenceTransformer(str(path), device="cpu", local_files_only=True)
        self.model.max_seq_length = config.CHUNK_TOKENS
        self.name = config.BI_ENCODER_MODEL
        self.token_counter = HFTokenCounter(self.model.tokenizer)
        self._cache: Dict[str, np.ndarray] = {}

    def encode(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.model.get_sentence_embedding_dimension() or 0), dtype=np.float32)
        prepared = [(config.BI_ENCODER_QUERY_PREFIX + t) if is_query else t for t in texts]
        keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in prepared]
        todo = [(k, t) for k, t in zip(keys, prepared) if k not in self._cache]
        if todo:
            vectors = self.model.encode(
                [t for _, t in todo],
                batch_size=16,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            for (key, _), vector in zip(todo, vectors):
                self._cache[key] = np.asarray(vector, dtype=np.float32)
        return np.vstack([self._cache[k] for k in keys])


@lru_cache(maxsize=4)
def load_encoder(mode: Optional[str] = None) -> Encoder:
    mode = mode or config.rag_mode()
    if mode == "fallback":
        return HashingEncoder()
    if model_present(config.BI_ENCODER_DIRNAME):
        try:
            return SentenceTransformerEncoder(model_path(config.BI_ENCODER_DIRNAME))
        except Exception as exc:  # pragma: no cover - depends on local install
            if mode == "real":
                raise ModelUnavailable(f"bi-encoder failed to load: {type(exc).__name__}") from exc
            return HashingEncoder()
    if mode == "real":
        raise ModelUnavailable("bi-encoder not downloaded: run scripts/download_models.py")
    return HashingEncoder()


class DenseIndex:
    """Normalized embedding matrix; cosine similarity is then a dot product."""

    def __init__(self, encoder: Encoder, texts: Sequence[str]) -> None:
        self.encoder = encoder
        self.matrix = encoder.encode(list(texts))

    def search(self, query: str, k: int = config.DENSE_TOP_K) -> List[Tuple[int, float]]:
        if self.matrix.shape[0] == 0:
            return []
        query_vec = self.encoder.encode([query], is_query=True)[0]
        sims = self.matrix @ query_vec
        order = sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))
        return [(i, float(sims[i])) for i in order[:k]]

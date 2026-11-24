"""Each RAG stage in isolation, checked against the spec parameters."""
from __future__ import annotations

import math

import numpy as np
import pytest

from pharma_intake import config
from pharma_intake.rag.bm25 import BM25Index
from pharma_intake.rag.chunker import RegexTokenCounter, split_windows
from pharma_intake.rag.dense import DenseIndex, HashingEncoder
from pharma_intake.rag.rerank import LexicalReranker, apply_threshold, sigmoid
from pharma_intake.rag.rrf import rrf_fuse
from pharma_intake.rag.text import tokenize


def test_spec_parameters():
    assert config.BM25_K1_RANGE[0] <= config.BM25_K1 <= config.BM25_K1_RANGE[1] == 1.5
    assert config.BM25_K1_RANGE == (1.2, 1.5) and config.BM25_B == 0.75
    assert (config.CHUNK_TOKENS, config.CHUNK_OVERLAP) == (512, 64)
    assert config.BM25_TOP_K == config.DENSE_TOP_K == 50
    assert config.RRF_K == 60
    assert config.RERANK_THRESHOLD == 0.40
    assert config.RERANK_TOP_K_RANGE == (3, 5) and 3 <= config.RERANK_TOP_K <= 5


# ---------------------------------------------------------------- tokenizer + BM25
def test_tokenizer_keeps_codes_whole_and_split():
    tokens = tokenize("Complaint for LOT-88392 under REF-2026-0001; Anaphylaxis reported.")
    assert "lot-88392" in tokens and "88392" in tokens and "ref-2026-0001" in tokens
    assert "anaphylaxis" in tokens and "for" not in tokens


@pytest.mark.parametrize("k1", [1.0, 1.19, 1.51, 2.0])
def test_bm25_rejects_k1_outside_spec(k1):
    with pytest.raises(ValueError):
        BM25Index([["a"]], k1=k1)


def test_bm25_exact_code_match_ranks_first():
    docs = [tokenize(t) for t in [
        "Cracked tablets in blister, batch LOT-40517.",
        "Leaking bottle reported for batch LOT-88392 by a pharmacist.",
        "Discoloured tablets from LOT-77215; batch quality concern.",
    ]]
    top = BM25Index(docs).top_k(tokenize("LOT-88392"), 3)
    assert top[0][0] == 1
    assert top[0][1] > 10 * top[1][1]        # other lots only share the generic "lot" part


def test_bm25_length_normalization_and_saturation():
    short = ["anaphylaxis", "report"]
    long = ["anaphylaxis", "report"] + [f"filler{i}" for i in range(40)]
    index = BM25Index([short, long, ["other"]])
    q = ["anaphylaxis"]
    assert index.score(q, 0) > index.score(q, 1)           # b = 0.75 penalises the long document
    sat = BM25Index([["x"] * 1 + ["y"], ["x"] * 10 + ["y"], ["z"]])
    ratio = sat.score(["x"], 1) / sat.score(["x"], 0)
    assert 1 < ratio < config.BM25_K1 + 1                   # k1 saturates term frequency


def test_bm25_idf_is_non_negative_and_ties_are_deterministic():
    index = BM25Index([["a", "b"], ["a", "b"], ["a"]])
    assert all(v >= 0 for v in index.idf.values())
    assert [i for i, _ in index.top_k(["b"], 5)] == [0, 1]


# ---------------------------------------------------------------- chunker
def test_chunk_windows_are_512_with_64_overlap():
    text = " ".join(f"word{i}" for i in range(1300))
    windows = split_windows(text, RegexTokenCounter())
    assert [w.token_count for w in windows[:-1]] == [512] * (len(windows) - 1)
    assert all(w.token_count <= 512 for w in windows)
    starts = [w.start_token for w in windows]
    assert all(b - a == 512 - 64 for a, b in zip(starts, starts[1:]))
    first, second = windows[0].text.split(), windows[1].text.split()
    assert first[-64:] == second[:64]                       # exactly 64 tokens shared
    assert windows[-1].text.endswith("word1299")
    assert all(w.text in text for w in windows)             # exact substrings


def test_short_text_is_one_chunk_and_bad_params_rejected():
    assert len(split_windows("A short section.", RegexTokenCounter())) == 1
    with pytest.raises(ValueError):
        split_windows("x", RegexTokenCounter(), size=64, overlap=64)


# ---------------------------------------------------------------- dense
def test_hashing_encoder_is_deterministic_and_normalized():
    enc = HashingEncoder()
    a, b = enc.encode(["Cracked tablets in blister"]), enc.encode(["Cracked tablets in blister"])
    assert np.allclose(a, b) and math.isclose(float(np.linalg.norm(a[0])), 1.0, rel_tol=1e-5)


def test_dense_index_uses_cosine_similarity():
    texts = ["leaking bottle cap", "cracked tablets blister", "missing leaflet carton"]
    hits = DenseIndex(HashingEncoder(), texts).search("cracked tablets in a blister", 3)
    assert hits[0][0] == 1 and -1.0 <= hits[-1][1] <= hits[0][1] <= 1.0


# ---------------------------------------------------------------- RRF
def test_rrf_formula_and_k():
    fused = {c.item_id: c for c in rrf_fuse({"bm25": ["a", "b", "c"], "dense": ["b", "a", "d"]}, k=60)}
    assert math.isclose(fused["a"].score, 1 / 61 + 1 / 62)
    assert math.isclose(fused["b"].score, 1 / 62 + 1 / 61)
    assert math.isclose(fused["d"].score, 1 / 63)
    assert fused["a"].ranks == {"bm25": 1, "dense": 2}


def test_rrf_needs_no_score_normalization_and_breaks_ties_by_id():
    order = [c.item_id for c in rrf_fuse({"bm25": ["x", "y"], "dense": ["y", "x"]})]
    assert order == ["x", "y"]


def test_rrf_uses_top_50_of_each_list():
    ranked = [f"d{i:03d}" for i in range(80)]
    fused = rrf_fuse({"bm25": ranked, "dense": ranked[::2]})   # dense: d000, d002, ... d078
    ids = {c.item_id for c in fused}
    assert {f"d{i:03d}" for i in range(50)} <= ids              # BM25 top-50
    assert "d078" in ids and "d098" not in ids                  # dense list has only 40 items
    assert "d051" not in ids and "d079" not in ids              # BM25 rank 52/80 is beyond top-50
    assert len(rrf_fuse({"dense": ranked})) == 50                # dense list truncated to top-50 too


# ---------------------------------------------------------------- reranker threshold
def test_threshold_and_top_k():
    scores = [0.10, 0.95, 0.40, 0.39, 0.80, 0.70, 0.60, 0.55]
    decisions, kept = apply_threshold(scores, 0.40, 5)
    assert kept == [1, 4, 5, 6, 7]                            # top 5 at or above 0.40, best first
    assert {d.index for d in decisions if not d.kept} == {0, 2, 3}
    assert apply_threshold([0.1, 0.39], 0.40, 5)[1] == []      # nothing relevant -> nothing passed


@pytest.mark.parametrize("top_k", [2, 6])
def test_top_k_must_be_3_to_5(top_k):
    with pytest.raises(ValueError):
        apply_threshold([0.9], 0.40, top_k)


def test_lexical_reranker_is_bounded_and_sigmoid_is_stable():
    scores = LexicalReranker().score("cracked tablets", ["cracked tablets in blister", "leaking bottle", ""])
    assert scores[0] == 1.0 and scores[1] == 0.0 and scores[2] == 0.0
    assert sigmoid(0) == 0.5 and 0.0 <= sigmoid(-800) < 1e-10 and sigmoid(800) == 1.0

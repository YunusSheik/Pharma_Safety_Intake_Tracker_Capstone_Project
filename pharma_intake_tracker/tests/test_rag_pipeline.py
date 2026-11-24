"""End-to-end 4-stage retrieval, grounded generation and the no-hallucination property."""
from __future__ import annotations

import pytest

from pharma_intake import config
from pharma_intake.errors import OfflineViolation
from pharma_intake.rag.generator import NO_ANSWER, Answer, ExtractiveGenerator, OllamaGenerator, ensure_loopback
from pharma_intake.rag.pipeline import HybridRAGPipeline
from pharma_intake.rag.prompt import SYSTEM_RULES, build_prompt
from pharma_intake.rag.text import split_sentences


@pytest.fixture
def indexed(repo, seeded, fallback_pipeline):
    fallback_pipeline.index(repo.list_reports())
    return fallback_pipeline


def test_corpus_contains_sops_and_past_reports(indexed, seeded):
    sources = {c.source_type for c in indexed.chunks}
    assert sources == {"sop", "report"}
    assert {c.doc_id for c in indexed.chunks if c.source_type == "report"} == set(seeded)
    assert all(c.token_count <= config.CHUNK_TOKENS for c in indexed.chunks)


def test_lot_lookup_through_all_four_stages(indexed):
    result = indexed.retrieve("LOT-88392")
    assert 0 < len(result.bm25) <= 50 and 0 < len(result.dense) <= 50
    assert len(result.fused) <= 100 and len(result.reranked) == len(result.fused)
    assert 1 <= len(result.final) <= 5
    assert all("LOT-88392" in c.text for c in result.final)
    assert all(s >= config.RERANK_THRESHOLD for s in result.final_scores)
    assert result.final_scores == sorted(result.final_scores, reverse=True)


def test_off_topic_query_returns_no_answer(indexed):
    rag = indexed.answer("What is the capital of France?")
    assert rag.retrieval.final == [] and rag.answer.text == NO_ANSWER
    assert "(no context passed the relevance threshold)" in rag.prompt


def test_extractive_answer_only_quotes_retrieved_text(indexed):
    rag = indexed.answer("Who can approve closing a report?")
    assert rag.retrieval.final and len(rag.answer.citations) == len(rag.retrieval.final)
    corpus = " ".join(" ".join(c.text.split()) for c in rag.retrieval.final)
    for line in rag.answer.text.splitlines()[1:]:
        quote = line[2:].rsplit(" [S", 1)[0]
        for sentence in split_sentences(quote):
            assert sentence in corpus                          # nothing invented


def test_prompt_is_grounded_and_tagged(indexed):
    result = indexed.retrieve("Who can approve closing a report?")
    prompt = build_prompt("Who can approve closing a report?", result.final)
    assert prompt.startswith(SYSTEM_RULES) and "[S1]" in prompt and "NEVER assess causality" in prompt


class _UnsafeGenerator:
    name = "unsafe-test-double"

    def generate(self, query, chunks, prompt):
        return Answer("The event was likely caused by the product.", [], self.name)


def test_output_guardrail_withholds_clinical_conclusions(indexed):
    pipeline = HybridRAGPipeline(indexed.encoder, indexed.reranker, _UnsafeGenerator())
    rag = pipeline.answer("Who can approve closing a report?")
    assert rag.output_blocked and rag.blocked_reason == "CAUSALITY"
    assert "caused by" not in rag.answer.text


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_ollama_must_be_loopback(url):
    assert ensure_loopback(url) == url


@pytest.mark.parametrize("url", ["https://api.example.com", "http://10.0.0.5:11434", "http://ollama.internal:11434"])
def test_remote_llm_endpoints_refused(url):
    with pytest.raises(OfflineViolation):
        OllamaGenerator(url=url)


def test_unreachable_local_ollama_falls_back_to_extractive(indexed):
    generator = OllamaGenerator(url="http://127.0.0.1:9", timeout=2)
    result = indexed.retrieve("Who can approve closing a report?")
    answer = generator.generate("Who can approve closing a report?", result.final, "prompt")
    assert answer.backend == "extractive" and "Ollama unavailable" in answer.note


def test_default_generator_is_extractive(indexed):
    assert isinstance(indexed.generator, ExtractiveGenerator)


# ---------------------------------------------------------------- real local models
@pytest.fixture(scope="module")
def real_indexed(tmp_path_factory, real_pipeline_factory):
    from scripts.seed import seed
    from pharma_intake.repository import Repository
    from pharma_intake.workflow import WorkflowService

    from conftest import TickClock

    repo = Repository(tmp_path_factory.mktemp("real") / "r.db", clock=TickClock())
    seed(repo, WorkflowService(repo))
    return real_pipeline_factory(repo.list_reports())


@pytest.mark.models
def test_real_models_are_loaded_offline(real_indexed):
    assert real_indexed.uses_real_models
    assert real_indexed.encoder.token_counter.name == "model-tokenizer"
    assert all(c.token_count <= 512 for c in real_indexed.chunks)


@pytest.mark.models
@pytest.mark.parametrize(
    "query,term",
    [
        ("Have there been any reports of difficulty breathing?", "dyspnea"),
        ("patient had difficulty breathing after taking the tablets", "dyspnea"),
        ("hives and itching after a dose", "urticaria"),
    ],
)
def test_semantic_retrieval_matches_colloquial_to_clinical_terms(real_indexed, query, term):
    result = real_indexed.retrieve(query)
    assert term not in query
    assert any(term in c.text.lower() for c in result.final)          # survives Stage 4
    top_dense = [h.chunk_id for h in result.dense[:3]]
    assert any(term in real_indexed._by_id[cid].text.lower() for cid in top_dense)


@pytest.mark.models
def test_real_reranker_filters_irrelevant_queries(real_indexed):
    result = real_indexed.retrieve("What is the capital of France?")
    assert result.final == []
    assert all(0.0 <= h.score <= 1.0 for h in result.reranked)

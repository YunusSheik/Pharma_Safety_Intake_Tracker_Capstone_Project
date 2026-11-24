"""Deterministic router (4 routes) and non-clinical guardrails (input + output)."""
from __future__ import annotations

import pytest

from pharma_intake import config
from pharma_intake.agent.guardrails import check_input, check_output
from pharma_intake.agent.router import EXEMPLARS, Route, SemanticRouter
from pharma_intake.rag.dense import HashingEncoder


@pytest.fixture(scope="module")
def router():
    return SemanticRouter(HashingEncoder())


@pytest.mark.parametrize(
    "message,route",
    [
        ("hello", Route.DIRECT),
        ("Good morning!", Route.DIRECT),
        ("thanks", Route.DIRECT),
        ("what can you do?", Route.DIRECT),
        ("What does SOP-004 say about closure approval?", Route.RAG),
        ("Which complaints involve LOT-88392?", Route.RAG),
        ("Have there been any reports of difficulty breathing?", Route.RAG),
        ("I want to submit a new adverse event report", Route.INTAKE),
        ("log a complaint: product=Zentravol; lot=LOT-12345", Route.INTAKE),
        ("type=PQC; product=Zentravol; lot=LOT-12345;", Route.INTAKE),
        ("Move REF-2026-0003 to In Review", Route.STATUS),
        ("what is the status of ref 2026-3", Route.STATUS),
        ("close REF-2026-0001", Route.STATUS),
    ],
)
def test_rule_tier_routes(router, message, route):
    decision = router.route(message)
    assert decision.route is route and decision.tier == "rule"


def test_semantic_tier_uses_fixed_exemplars(router):
    phrase = EXEMPLARS[Route.INTAKE][1]             # matches no rule
    decision = router.route(phrase)
    assert decision.tier == "semantic" and decision.route is Route.INTAKE and decision.score >= 0.99


def test_unmatched_messages_default_to_hybrid_rag(router):
    decision = router.route("zxqv blorp")
    assert decision.route is Route.RAG and decision.tier == "default"


def test_router_is_deterministic(router):
    decisions = {router.route("Move REF-2026-0003 to In Review") for _ in range(10)}
    decisions |= {router.route("Move REF-2026-0003 to In Review") for _ in range(10)}
    assert len(decisions) == 1


def test_router_without_encoder_still_routes():
    assert SemanticRouter(None).route("random words here").route is Route.RAG


# ---------------------------------------------------------------- guardrails
@pytest.mark.parametrize(
    "message,reason",
    [
        ("Did Zentravol cause the rash?", "CAUSALITY"),
        ("Was the dyspnea caused by the product?", "CAUSALITY"),
        ("Is this event related to the drug?", "CAUSALITY"),
        ("Please assess causality for REF-2026-0001", "CAUSALITY"),
        ("Could the tablets have led to the fainting?", "CAUSALITY"),
        ("What treatment should the patient get?", "TREATMENT"),
        ("Should the patient stop taking Clarivex?", "TREATMENT"),
        ("Recommend a dose for the consumer", "TREATMENT"),
        ("Is this reportable?", "REPORTABILITY"),
        ("Should REF-2026-0004 be reported to the FDA?", "REPORTABILITY"),
        ("Is REF-2026-0004 serious?", "REPORTABILITY"),
        ("Determine the seriousness of this case", "REPORTABILITY"),
        ("Can you diagnose the reaction?", "CLINICAL_DECISION"),
    ],
)
def test_input_guardrail_blocks_clinical_and_regulatory_requests(message, reason):
    verdict = check_input(message)
    assert not verdict.allowed and verdict.reason == reason and verdict.message


@pytest.mark.parametrize(
    "message",
    [
        "Which complaints involve LOT-88392?",
        "Have there been any reports of difficulty breathing?",
        "What reports were due to close this week?",
        "How should I record a telephone complaint?",
        "Move REF-2026-0001 to In Review",
        "What does SOP-004 say about closure approval?",
        "Which SOP is related to the product quality complaint process?",
        "Submit a new report: type=PQC; product=Zentravol; lot=LOT-12345",
        "hello",
    ],
)
def test_input_guardrail_allows_in_scope_requests(message):
    assert check_input(message).allowed


@pytest.mark.parametrize(
    "text",
    [
        "The rash was likely caused by Zentravol.",
        "The event is causally related to the product.",
        "This case is reportable.",
        "We recommend the patient stop the medicine.",
        "It must be reported to the authority within 15 days.",
    ],
)
def test_output_guardrail_blocks_clinical_conclusions(text):
    assert not check_output(text).allowed


def test_corpus_does_not_trip_the_output_guardrail():
    """Extractive answers quote the SOPs verbatim, so the SOPs themselves must pass."""
    for path in sorted(config.SOP_DIR.glob("SOP-*.md")):
        assert check_output(path.read_text(encoding="utf-8")).allowed, path.name


def test_seed_narratives_are_clean():
    from pharma_intake.pii import find_identifiers
    from scripts.seed import SEED_REPORTS

    for item in SEED_REPORTS:
        for key in ("description", "event_description"):
            text = item["data"].get(key)
            if text:
                assert check_output(text).allowed and find_identifiers(text) == [], text

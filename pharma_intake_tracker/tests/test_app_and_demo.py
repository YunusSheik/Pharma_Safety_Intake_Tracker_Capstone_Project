"""UI smoke/integration tests (Streamlit AppTest) and the demo evidence script."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pharma_intake.repository import Repository

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PHARMA_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PHARMA_RAG_MODE", "fallback")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _submit(at):
    next(b for b in at.button if b.label == "Validate and save").click().run()
    assert not at.exception, at.exception


def test_app_loads_all_tabs_with_disclaimer(app):
    assert [t.label for t in app.tabs] == [
        "📝 New Intake", "📊 Dashboard", "🔁 Triage Workflow", "🤖 Knowledge Assistant", "🧾 Audit Trail"]
    assert any("fictional data only" in w.value for w in app.sidebar.warning)


def test_intake_reserves_reference_validates_and_saves(app, tmp_path):
    app.button(key="start_report").click().run()
    assert any("REF-" in h.value for h in app.subheader)

    _submit(app)                                                  # empty form -> context-sensitive errors
    assert any("need attention" in e.value for e in app.error)
    assert any("Product name" in m.value for m in app.markdown)

    app.radio(key="rt_0").set_value("Product Quality Complaint").run()
    app.text_input(key="pn_0").input("Zentravol 50 mg tablets")
    app.text_input(key="lot_0").input("LOT-40517")
    app.date_input(key="dr_0").set_value(date.today())
    app.selectbox(key="rp_0").set_value("Pharmacist")
    app.selectbox(key="ch_0").set_value("Email")
    app.text_area(key="desc_0").input("Several tablets in a sealed blister were cracked on opening.")
    _submit(app)
    assert any("Saved REF-" in s.value for s in app.success)

    reports = Repository(tmp_path / "ui.db").list_reports()
    assert len(reports) == 1 and reports[0].batch_lot_number == "LOT-40517"


def test_chat_greeting_and_guardrail(app):
    app.chat_input(key="chat").set_value("hello").run()
    assert not app.exception
    app.chat_input(key="chat").set_value("Should this be reported to the FDA?").run()
    assert not app.exception
    texts = " ".join(m.value for m in app.markdown)
    assert "intake knowledge assistant" in texts and "can't determine regulatory reportability" in texts


def test_demo_script_produces_all_evidence_sections(tmp_path):
    from scripts.demo_run import run

    text = run(mode="fallback", out=tmp_path / "DEMO_OUTPUT.md")
    for section in ("1. Two fictional reports", "2. Status transition", "3. Multi-stage hybrid RAG",
                    "4. Agentic control plane", "5. Audit records", "Hash-chain verification:** ok=True",
                    "blocked by trigger", "CLOSURE_APPROVED", "CIRCUIT_BREAKER_TRIPPED"):
        assert section in text, section
    assert (tmp_path / "DEMO_OUTPUT.md").exists()

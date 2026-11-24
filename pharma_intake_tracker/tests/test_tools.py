"""Type-safe tool validation: auto-repair, structured errors, no approval capability."""
from __future__ import annotations

import pytest

from pharma_intake.tools import TOOLS, ToolExecutor, auto_repair


@pytest.fixture
def executor(repo, workflow, fallback_pipeline):
    return ToolExecutor(repo, workflow, fallback_pipeline, "OP-001")


@pytest.mark.parametrize(
    "raw,expected",
    [("lot 88392", "LOT-88392"), ("Lot_88392", "LOT-88392"), ("lot-88392", "LOT-88392"),
     ("LOT:88392", "LOT-88392"), ("88392", "LOT-88392"), ("LOT-8839", "LOT-8839")],
)
def test_lot_auto_repair(raw, expected):
    args, _ = auto_repair("submit_intake_report", {"batch_lot_number": raw})
    assert args["batch_lot_number"] == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("ref-2026-7", "REF-2026-0007"), ("REF 2026 0042", "REF-2026-0042"), ("ref_2026_0001", "REF-2026-0001"),
     ("REF-2026-0003", "REF-2026-0003"), ("case 7", "case 7")],
)
def test_reference_auto_repair(raw, expected):
    args, _ = auto_repair("get_report", {"ref_id": raw})
    assert args["ref_id"] == expected


@pytest.mark.parametrize("raw,expected", [("in-review", "In Review"), ("IN_REVIEW", "In Review"),
                                          ("closed", "Closed"), ("close", "Closed"), ("archived", "archived")])
def test_status_auto_repair(raw, expected):
    args, _ = auto_repair("update_status", {"ref_id": "REF-2026-0001", "new_status": raw})
    assert args["new_status"] == expected


def test_key_aliases_and_typos_repaired_and_recorded():
    args, repairs = auto_repair(
        "submit_intake_report",
        {"type": "pqc", "batch_lot_numbr": "LOT-12345", "Product": "Zentravol", "received": "2026/11/2"},
    )
    assert args == {"report_type": "Product Quality Complaint", "batch_lot_number": "LOT-12345",
                    "product_name": "Zentravol", "date_received": "2026-11-02"}
    assert any("batch_lot_numbr" in r for r in repairs) and any("'pqc'" in r for r in repairs)


def test_free_text_values_are_masked_in_repair_log():
    _, repairs = auto_repair("submit_intake_report", {"desc": "Secret-ish narrative text"})
    assert repairs == ["argument name 'desc' -> 'description'"]


def test_update_status_success(executor, make_report):
    ref = make_report()
    result = executor.call("update_status", {"ref": ref.lower(), "status": "in review"})
    assert result.ok and result.data["to_status"] == "In Review"
    assert result.repairs


def test_invalid_reference_returns_structured_error(executor):
    result = executor.call("update_status", {"ref_id": "case-7", "new_status": "In Review"})
    assert not result.ok and result.error.code == "validation_error"
    assert result.error.fields[0]["field"] == "ref_id"
    assert "REF-YYYY-NNNN" in result.error.fields[0]["message"]


def test_unknown_argument_rejected(executor):
    result = executor.call("get_report", {"ref_id": "REF-2026-0001", "approve": True})
    assert not result.ok and result.error.fields[0]["field"] == "approve"


def test_domain_error_is_structured(executor, make_report):
    ref = make_report()
    result = executor.call("update_status", {"ref_id": ref, "new_status": "Closed"})
    assert not result.ok and result.error.code == "domain_error"
    assert "Allowed next status" in result.error.message


def test_submit_via_tool(repo, executor, valid_pqc):
    result = executor.call("submit_intake_report", valid_pqc())
    assert result.ok
    ref = result.data["ref_id"]
    assert repo.get_report(ref).created_by == "OP-001"
    created = [e for e in repo.audit_entries(ref_id=ref) if e.action == "REPORT_CREATED"][0]
    assert created.details["via"] == "agent"


def test_invalid_submit_consumes_no_reference(repo, executor, valid_pqc):
    result = executor.call("submit_intake_report", valid_pqc(batch_lot_number="LOT-1", reporter_type=None))
    assert not result.ok
    assert {f["field"] for f in result.error.fields} == {"batch_lot_number", "reporter_type"}
    with repo.reader() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_reservations").fetchone()[0] == 0
    failed = [e for e in repo.audit_entries() if e.action == "TOOL_CALL_FAILED"][0]
    assert failed.details["fields"] == ["batch_lot_number", "reporter_type"]


def test_agent_has_no_approval_tool(executor, make_report, workflow, repo):
    assert "approve_closure" not in TOOLS
    result = executor.call("approve_closure", {"ref_id": "REF-2026-0001"})
    assert not result.ok and result.error.code == "unknown_tool"


def test_search_knowledge_audits_fingerprint_only(repo, executor):
    result = executor.call("search_knowledge", {"query": "Who can approve closing a report?"})
    assert result.ok and result.data["result_count"] >= 1
    entry = [e for e in repo.audit_entries() if e.action == "RAG_QUERY"][0]
    assert set(entry.details) >= {"query_sha256", "query_length", "result_count", "stage_counts", "mode"}
    assert "approve" not in str(entry.details).lower()

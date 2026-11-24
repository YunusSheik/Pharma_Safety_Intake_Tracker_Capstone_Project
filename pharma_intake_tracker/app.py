"""Pharma Safety Intake & Triage Tracker — Streamlit UI (local training prototype).

Run from this folder:  streamlit run app.py
The UI holds no business logic. It calls Repository, WorkflowService and the
Assistant, which enforce every rule (validation, lifecycle, HITL, audit).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from pharma_intake import config  # noqa: E402
from pharma_intake.agent.assistant import Assistant, AssistantReply  # noqa: E402
from pharma_intake.errors import (  # noqa: E402
    ApprovalError,
    ApprovalPending,
    IntakeValidationError,
    InvalidTransition,
    PharmaIntakeError,
)
from pharma_intake.rag.pipeline import HybridRAGPipeline, build_pipeline  # noqa: E402
from pharma_intake.repository import Repository  # noqa: E402
from pharma_intake.schemas import FIELD_LABELS, IntakeChannel, ReporterType, ReportType, Status  # noqa: E402
from pharma_intake.workflow import REJECTION_REASONS, WorkflowService, allowed_next  # noqa: E402

EDIT_LOG = ROOT.parent / "logs" / "edit_audit.log"

st.set_page_config(page_title="Pharma Safety Intake & Triage Tracker", page_icon="🧾", layout="wide")


# ---------------------------------------------------------------- shared resources
@st.cache_resource
def get_repo(path: str) -> Repository:
    return Repository(path)


@st.cache_resource(show_spinner="Loading local RAG models (offline)…")
def get_pipeline(mode: str, backend: str) -> HybridRAGPipeline:
    return build_pipeline((), mode=mode, backend=backend)


repo = get_repo(str(config.db_path()))
workflow = WorkflowService(repo)
pipeline = get_pipeline(config.rag_mode(), config.llm_backend())

ss = st.session_state
ss.setdefault("draft_ref", None)
ss.setdefault("form_version", 0)
ss.setdefault("last_saved", None)
ss.setdefault("messages", [])


def get_assistant(actor: str) -> Assistant:
    assistant: Optional[Assistant] = ss.get("assistant")
    if assistant is None:
        assistant = Assistant(repo, workflow, pipeline, actor)
        ss["assistant"] = assistant
    assistant.actor = actor
    assistant.executor.actor = actor
    return assistant


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🧾 Intake & Triage Tracker")
    operator = st.selectbox("Acting operator (pseudonymous)", config.OPERATORS, key="operator")
    assistant = get_assistant(operator)

    if st.button("Clear chat", key="clear_chat"):
        ss["messages"] = []
        assistant.memory.clear()
        st.rerun()

    st.subheader("Data")
    counts = repo.status_counts()
    st.caption(" · ".join(f"{k}: {v}" for k, v in counts.items()))
    if sum(counts.values()) == 0 and st.button("Load fictional history (seed)", key="seed_btn"):
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed as seed_module  # noqa: E402

        seed_module.seed(repo, workflow)
        st.rerun()


# ---------------------------------------------------------------- helpers
def show_issues(issues: List[Any]) -> None:
    st.error(f"The report was not saved — {len(issues)} field(s) need attention:", icon="🚫")
    for issue in issues:
        st.markdown(f"- **{FIELD_LABELS.get(issue.field, issue.field)}** — {issue.message}")


def reports_frame(reports: List[Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Reference": r.ref_id,
                "Type": r.report_type.value,
                "Product": r.product_name,
                "Batch/lot": r.batch_lot_number,
                "Received": r.date_received.isoformat(),
                "Reporter type": r.reporter_type.value,
                "Channel": r.intake_channel.value,
                "Status": r.status.value,
                "Closure pending": "Yes" if r.pending_closure else "",
                "Created by": r.created_by,
            }
            for r in reports
        ]
    )


# ---------------------------------------------------------------- tabs
tab_intake, tab_dash, tab_flow, tab_chat, tab_audit = st.tabs(
    ["📝 New Intake", "📊 Dashboard", "🔁 Triage Workflow", "🤖 Knowledge Assistant", "🧾 Audit Trail"]
)

# ---- New Intake -------------------------------------------------------------
with tab_intake:
    st.header("New intake report")
    if ss["last_saved"]:
        st.success(f"Saved {ss['last_saved']} with status New. It now appears on the Dashboard.", icon="✅")
    if ss["draft_ref"] is None:
        st.write("Starting a report reserves a unique reference number immediately (SOP-001).")
        if st.button("Start new report", type="primary", key="start_report"):
            ss["draft_ref"] = repo.reserve_reference(operator)
            ss["last_saved"] = None
            st.rerun()
    else:
        ref_id = ss["draft_ref"]
        st.subheader(f"Reference: `{ref_id}`")
        st.caption("Reserved on initiation. Enter synthetic, de-identified information only.")
        v = ss["form_version"]
        report_type = st.radio(
            "Report type *", [t.value for t in ReportType], index=None, horizontal=True, key=f"rt_{v}"
        )
        is_ae = report_type == ReportType.ADVERSE_EVENT.value
        with st.form(f"intake_form_{v}", clear_on_submit=False):
            c1, c2 = st.columns(2)
            product = c1.text_input("Product name *", placeholder="e.g. Zentravol 50 mg tablets", key=f"pn_{v}")
            lot = c2.text_input("Batch/lot number *", placeholder="LOT-12345", key=f"lot_{v}")
            c3, c4, c5 = st.columns(3)
            received = c3.date_input("Date received *", value=None, max_value=date.today(), key=f"dr_{v}")
            reporter = c4.selectbox("Reporter type *", [r.value for r in ReporterType], index=None,
                                    placeholder="Select a category", key=f"rp_{v}")
            channel = c5.selectbox("Intake channel *", [c.value for c in IntakeChannel], index=None,
                                   placeholder="Email or Telephone", key=f"ch_{v}")
            description = st.text_area("Description *", placeholder="What was reported (no names, contact "
                                       "details, ages or dates of birth).", key=f"desc_{v}")
            event_description = event_onset = None
            if is_ae:
                st.markdown("**Adverse event details** (required for Suspected Adverse Event)")
                event_description = st.text_area("Event description *", key=f"ev_{v}")
                event_onset = st.date_input("Event onset date *", value=None, max_value=date.today(),
                                            key=f"eo_{v}")
            submitted = st.form_submit_button("Validate and save", type="primary")
        if st.button("Discard draft", key="discard_draft"):
            ss["draft_ref"] = None
            ss["form_version"] += 1
            st.rerun()
        if submitted:
            data: Dict[str, Any] = {
                "report_type": report_type,
                "product_name": product,
                "batch_lot_number": lot,
                "date_received": received.isoformat() if received else None,
                "reporter_type": reporter,
                "intake_channel": channel,
                "description": description,
            }
            if is_ae:
                data["event_description"] = event_description
                data["event_onset_date"] = event_onset.isoformat() if event_onset else None
            try:
                saved = repo.submit_report(ref_id, data, operator)
            except IntakeValidationError as exc:
                show_issues(exc.issues)
            except PharmaIntakeError as exc:
                st.error(str(exc))
            else:
                ss["last_saved"] = saved.ref_id
                ss["draft_ref"] = None
                ss["form_version"] += 1
                st.rerun()

# ---- Dashboard --------------------------------------------------------------
with tab_dash:
    st.header("Tracker dashboard")
    counts = repo.status_counts()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("New", counts["New"])
    m2.metric("In Review", counts["In Review"])
    m3.metric("Closed", counts["Closed"])
    m4.metric("Closure pending (HITL)", len(workflow.pending_approvals()))
    f1, f2 = st.columns(2)
    status_filter = f1.multiselect("Filter by status", [s.value for s in Status], default=[s.value for s in Status],
                                   key="flt_status")
    type_filter = f2.multiselect("Filter by report type", [t.value for t in ReportType],
                                 default=[t.value for t in ReportType], key="flt_type")
    reports = repo.list_reports(statuses=status_filter or None, report_types=type_filter or None) \
        if status_filter and type_filter else []
    st.caption(f"{len(reports)} report(s) shown")
    if reports:
        st.dataframe(reports_frame(reports), hide_index=True, width="stretch")
        chosen = st.selectbox("Report details", [r.ref_id for r in reports], index=None,
                              placeholder="Select a reference", key="detail_ref")
        if chosen:
            rep = repo.get_report(chosen)
            st.json({k: (v.value if hasattr(v, "value") else str(v) if v is not None else None)
                     for k, v in rep.model_dump().items()})
            st.markdown("**Status history**")
            st.dataframe(pd.DataFrame(repo.status_history(chosen)), hide_index=True)

# ---- Triage Workflow --------------------------------------------------------
with tab_flow:
    st.header("Triage workflow")
    st.caption("Allowed transitions: New → In Review → Closed. Closing always requires a human reviewer (HITL).")
    left, right = st.columns(2)
    with left:
        st.subheader("Advance status")
        open_reports = [r for r in repo.list_reports(statuses=["New", "In Review"]) if not r.pending_closure]
        if not open_reports:
            st.info("No reports awaiting a transition.")
        else:
            target_ref = st.selectbox(
                "Report", [r.ref_id for r in open_reports], key="wf_ref",
                format_func=lambda ref: next(f"{r.ref_id} · {r.status.value} · {r.product_name}"
                                             for r in open_reports if r.ref_id == ref),
            )
            current = next(r for r in open_reports if r.ref_id == target_ref)
            nxt = allowed_next(current.status)[0]
            label = "Move to In Review" if nxt is Status.IN_REVIEW else "Request closure (opens HITL gate)"
            if st.button(label, type="primary", key="wf_advance"):
                try:
                    outcome = workflow.advance(target_ref, nxt, operator)
                    st.success(outcome.message)
                except (InvalidTransition, ApprovalPending) as exc:
                    st.error(str(exc))
    with right:
        st.subheader("Closure approvals (HITL)")
        reviewer = st.selectbox("Reviewer identity", config.REVIEWERS, key="reviewer")
        pending = workflow.pending_approvals()
        if not pending:
            st.info("No closure requests pending.")
        for req in pending:
            rep = repo.get_report(req["ref_id"])
            with st.container(border=True):
                st.markdown(f"**{rep.ref_id}** · {rep.report_type.value} · {rep.product_name} · {rep.batch_lot_number}")
                st.caption(f"Requested by {req['requested_by']} at {req['requested_at']} — report is frozen.")
                attest = st.checkbox(
                    "I attest the intake record is complete and was reviewed within this tool's non-clinical scope.",
                    key=f"att_{rep.ref_id}")
                a1, a2, a3 = st.columns([1, 1, 1])
                if a1.button("Approve closure", type="primary", key=f"approve_{rep.ref_id}"):
                    try:
                        st.success(workflow.approve_closure(rep.ref_id, reviewer, attestation=attest).message)
                        st.rerun()
                    except ApprovalError as exc:
                        st.error(str(exc))
                reason = a2.selectbox("Reason", REJECTION_REASONS, key=f"rr_{rep.ref_id}",
                                      label_visibility="collapsed")
                if a3.button("Reject", key=f"reject_{rep.ref_id}"):
                    try:
                        st.warning(workflow.reject_closure(rep.ref_id, reviewer, reason).message)
                        st.rerun()
                    except ApprovalError as exc:
                        st.error(str(exc))

# ---- Knowledge Assistant ------------------------------------------------------
with tab_chat:
    st.header("Knowledge assistant")
    examples = [
        "Have there been any reports of difficulty breathing?",
        "Which complaints involve LOT-88392?",
        "Who can approve closing a report?",
        "What is the status of REF-2026-0003?",
    ]
    cols = st.columns(len(examples))
    clicked = None
    for col, example in zip(cols, examples):
        if col.button(example, key=f"ex_{example}"):
            clicked = example
    for turn in ss["messages"]:
        with st.chat_message(turn["role"]):
            if turn["role"] == "assistant":
                reply: AssistantReply = turn["reply"]
                st.markdown(reply.text)
            else:
                st.markdown(turn["text"])
    prompt = st.chat_input("Ask about SOPs or past reports, submit a report, or update a status…", key="chat")
    message = prompt or clicked
    if message:
        reply = assistant.handle(message)
        ss["messages"].append({"role": "user", "text": message})
        ss["messages"].append({"role": "assistant", "reply": reply})
        st.rerun()

# ---- Audit Trail ----------------------------------------------------------------
with tab_audit:
    st.header("Audit trail (append-only, hash-chained)")
    a1, a2 = st.columns([2, 1])
    ref_filter = a1.text_input("Filter by reference", placeholder="REF-2026-0001", key="audit_ref").strip() or None
    if a2.button("Verify hash chain", key="verify_chain"):
        report = repo.verify_audit_chain()
        if report.ok:
            a2.success(f"Chain intact — {report.checked} entries verified.")
        else:
            a2.error(f"Chain broken at entry {report.first_bad_id}: {report.reason}")
    entries = repo.audit_entries(ref_id=ref_filter)
    st.caption(f"{len(entries)} entries · details contain field names, statuses and hashes only — never free text.")
    if entries:
        st.dataframe(
            pd.DataFrame([
                {"#": e.id, "Timestamp (UTC)": e.ts, "Actor": e.actor, "Action": e.action,
                 "Reference": e.ref_id or "", "Details": str(e.details), "Hash": e.entry_hash[:12] + "…"}
                for e in reversed(entries)
            ]),
            hide_index=True, width="stretch",
        )

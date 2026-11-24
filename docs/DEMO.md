# Demo walkthrough (live, about 10 minutes)

Two kinds of demonstration evidence:
1. **Reproducible transcript:** `python scripts/demo_run.py` regenerates [DEMO_OUTPUT.md](DEMO_OUTPUT.md) from scratch against a throwaway database.
2. **Live UI walkthrough:** the script below. Take a screenshot at each 📸 for the submission.

## Setup
```bash
cd pharma_intake_tracker
python scripts/seed.py --reset      # 15 fictional historical reports through the real API
streamlit run app.py                # http://127.0.0.1:8501
```
The sidebar should show **"Real local models loaded (offline)"**. If it shows fallback mode, run `python scripts/download_models.py` first.

## 1. Two fictional reports (validation + persistence)
1. **New Intake** → *Start new report*. The reference (e.g. `REF-2026-0016`) appears before any data is entered. 📸
2. Choose **Suspected Adverse Event**, type a product only, and click *Validate and save*. Each missing field gets its own message, including *"Event onset date is required for Suspected Adverse Event reports."* 📸
3. Type `Call me on +44 20 7946 0958` in the description. It is rejected as a *phone number* (the value is never echoed back).
4. Complete the form with fictional data, e.g. *Velmiradine 2% cream*, `LOT-64090`, Consumer, Email, "Consumer reported redness and itching at the application site after the second use.", event "Application site erythema and pruritus.", onset the day before receipt. Save → **New**. 📸
5. Submit a second report, a **Product Quality Complaint**, through the **Knowledge Assistant** chat:
   `Submit a new report: type=pqc; product=Ophtiralin 0.5% eye drops; lot=lot 12977; received=2026/11/22; reporter=pharmacist; channel=phone; description=Pharmacist found a cracked dropper bottle inside a sealed carton.`
   Open the ReAct trace to see the auto-repairs (`lot 12977 → LOT-12977`, `pqc → Product Quality Complaint`, date format). 📸

## 2. Dashboard
**Dashboard** → filter *Status = New* and *Type = Suspected Adverse Event*, then open the details of the new report (record plus status history). 📸

## 3. Status transition through the HITL gate
1. **Triage Workflow** → select the AE report → *Move to In Review*.
2. *Request closure (opens HITL gate)*. The report leaves the advance list (frozen) and appears under **Closure approvals**. 📸
3. Click *Approve closure* **without** ticking the attestation → refused.
4. Tick the attestation as reviewer `REV-01` → *Approve closure* → **Closed**. 📸
5. Chat: `close REF-2026-0016` on a Closed report → "Closed is final".

## 4. Multi-stage RAG query
Chat: **"Have there been any reports of difficulty breathing?"**
- The answer quotes the past report that says *dyspnea*, with a `[S1]` citation.
- Open **Retrieval diagnostics**: BM25 ranks it lower (no shared clinical word), the bi-encoder ranks it #1, RRF fuses them, and the cross-encoder keeps it above 0.40 and drops the rest. 📸
- Open **Grounded generation prompt** to show what a generator would receive.
- Then try `Which complaints involve LOT-88392?` (exact-code recall) and `What is the capital of France?` ("No sufficiently relevant record found").

## 5. Control plane
- `Did Velmiradine cause the erythema?` → **Guardrail: CAUSALITY** refusal (no tool runs). 📸
- `Should this be reported to the FDA?` → **REPORTABILITY** refusal.
- Send `move REF-2026-9999 to in review` three times. On the 3rd, the **circuit breaker opens**: the sidebar turns red and even valid questions are refused. Click **Reset agent** in the sidebar to recover. 📸

## 6. Audit records
**Audit Trail** → filter by the AE reference: `REFERENCE_RESERVED → VALIDATION_FAILED → REPORT_CREATED → STATUS_CHANGED → CLOSURE_REQUESTED → CLOSURE_APPROVED (REV-01, attestation) → STATUS_CHANGED`. Click *Verify hash chain* → intact. Expand **Development governance log** to show the PostToolUse hook entries. 📸

## Review skill (Deliverable 4)
In Claude Code, at the repository root: `/pharma-intake-review`. The result is appended to [review-evidence.md](review-evidence.md).

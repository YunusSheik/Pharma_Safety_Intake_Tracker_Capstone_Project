"""Seed the local database with FICTIONAL historical reports (the RAG "past reports" corpus).

Everything goes through the real Repository/WorkflowService API, so the seeded
history, HITL approvals and audit chain are identical to what the UI produces.

    python scripts/seed.py            # seed data/tracker.db if it is empty
    python scripts/seed.py --reset    # delete the DB first
    python scripts/seed.py --db path/to/other.db
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pharma_intake import config  # noqa: E402
from pharma_intake.repository import Repository  # noqa: E402
from pharma_intake.workflow import WorkflowService  # noqa: E402

AE = "Suspected Adverse Event"
PQC = "Product Quality Complaint"

# (days_before_today_received, onset_offset_days, target_status, record)
# target_status: New | In Review | Pending | Closed
SEED_REPORTS: List[Dict[str, Any]] = [
    {"age": 40, "onset": 1, "status": "Closed", "data": {
        "report_type": AE, "product_name": "Zentravol 50 mg tablets", "batch_lot_number": "LOT-88392",
        "reporter_type": "Pharmacist", "intake_channel": "Telephone",
        "description": "Pharmacist relayed a consumer report of dyspnea and wheezing that started about two hours after the first tablet.",
        "event_description": "Dyspnea and wheezing reported; the reporter states the symptoms subsided the same evening."}},
    {"age": 36, "onset": 2, "status": "Closed", "data": {
        "report_type": AE, "product_name": "Clarivex 10 mg/mL oral solution", "batch_lot_number": "LOT-51207",
        "reporter_type": "Consumer", "intake_channel": "Email",
        "description": "Consumer reported widespread urticaria and pruritus on both arms after the third dose.",
        "event_description": "Urticaria and pruritus on both arms, reported as resolving over two days."}},
    {"age": 33, "onset": 3, "status": "In Review", "data": {
        "report_type": AE, "product_name": "Nebrastin 20 mg capsules", "batch_lot_number": "LOT-30418",
        "reporter_type": "Healthcare Professional", "intake_channel": "Telephone",
        "description": "Healthcare professional reported nausea and emesis during the first week of use.",
        "event_description": "Nausea and emesis reported during the first week; no further detail provided."}},
    {"age": 30, "onset": 0, "status": "Pending", "data": {
        "report_type": AE, "product_name": "Zentravol 50 mg tablets", "batch_lot_number": "LOT-88392",
        "reporter_type": "Healthcare Professional", "intake_channel": "Email",
        "description": "Clinic nurse reported a single episode of syncope shortly after a dose; recovery within minutes per the reporter.",
        "event_description": "Single syncope episode with recovery within minutes, as described by the reporter."}},
    {"age": 26, "onset": 2, "status": "In Review", "data": {
        "report_type": AE, "product_name": "Quorvantia 5 mg tablets", "batch_lot_number": "LOT-77215",
        "reporter_type": "Consumer", "intake_channel": "Telephone",
        "description": "Caller described persistent headache and dizziness beginning two days after starting the product.",
        "event_description": "Persistent headache and dizziness, ongoing at the time of the call."}},
    {"age": 21, "onset": 1, "status": "New", "data": {
        "report_type": AE, "product_name": "Velmiradine 2% cream", "batch_lot_number": "LOT-64033",
        "reporter_type": "Pharmacist", "intake_channel": "Email",
        "description": "Pharmacist reported erythema and a burning sensation at the application site after first use.",
        "event_description": "Application site erythema and burning sensation after the first application."}},
    {"age": 12, "onset": 0, "status": "New", "data": {
        "report_type": AE, "product_name": "Clarivex 10 mg/mL oral solution", "batch_lot_number": "LOT-51207",
        "reporter_type": "Healthcare Professional", "intake_channel": "Telephone",
        "description": "Physician reported a suspected anaphylaxis episode with facial swelling shortly after administration.",
        "event_description": "Suspected anaphylaxis with facial swelling; emergency services were contacted by the household."}},
    {"age": 38, "status": "Closed", "data": {
        "report_type": PQC, "product_name": "Zentravol 50 mg tablets", "batch_lot_number": "LOT-88392",
        "reporter_type": "Pharmacist", "intake_channel": "Email",
        "description": "Pharmacist found several cracked and chipped tablets inside a sealed blister pack; a sample is available for return."}},
    {"age": 29, "status": "In Review", "data": {
        "report_type": PQC, "product_name": "Nebrastin 20 mg capsules", "batch_lot_number": "LOT-30419",
        "reporter_type": "Consumer", "intake_channel": "Telephone",
        "description": "Consumer found two capsules that appeared empty in a newly opened bottle."}},
    {"age": 24, "status": "Closed", "data": {
        "report_type": PQC, "product_name": "Clarivex 10 mg/mL oral solution", "batch_lot_number": "LOT-51288",
        "reporter_type": "Sales Representative", "intake_channel": "Email",
        "description": "Bottle arrived leaking around the cap and the label was partly illegible because of the liquid."}},
    {"age": 18, "status": "Pending", "data": {
        "report_type": PQC, "product_name": "Quorvantia 5 mg tablets", "batch_lot_number": "LOT-77215",
        "reporter_type": "Pharmacist", "intake_channel": "Telephone",
        "description": "Tablets show brown specks and discolouration not seen in previous packs of the same product."}},
    {"age": 15, "status": "In Review", "data": {
        "report_type": PQC, "product_name": "Velmiradine 2% cream", "batch_lot_number": "LOT-64033",
        "reporter_type": "Consumer", "intake_channel": "Email",
        "description": "Tube crimp seal was split on arrival and the cream had an unusual odour."}},
    {"age": 9, "status": "New", "data": {
        "report_type": PQC, "product_name": "Ophtiralin 0.5% eye drops", "batch_lot_number": "LOT-12950",
        "reporter_type": "Healthcare Professional", "intake_channel": "Telephone",
        "description": "Dropper tip fails to dispense unless the bottle is squeezed very hard."}},
    {"age": 6, "status": "New", "data": {
        "report_type": PQC, "product_name": "Zentravol 50 mg tablets", "batch_lot_number": "LOT-88401",
        "reporter_type": "Pharmacist", "intake_channel": "Email",
        "description": "Patient information leaflet missing from a sealed carton."}},
    {"age": 3, "status": "New", "data": {
        "report_type": PQC, "product_name": "Nebrastin 20 mg capsules", "batch_lot_number": "LOT-30418",
        "reporter_type": "Consumer", "intake_channel": "Telephone",
        "description": "Foreign black particle observed inside one capsule shell."}},
]


def seed(repo: Repository, workflow: WorkflowService) -> List[str]:
    """Insert every seed report and drive it to its target status. Returns the REF ids."""
    today = repo.today()
    refs: List[str] = []
    for index, item in enumerate(SEED_REPORTS):
        operator = config.OPERATORS[index % len(config.OPERATORS)]
        reviewer = config.REVIEWERS[index % len(config.REVIEWERS)]
        received = today - timedelta(days=item["age"])
        data = dict(item["data"], date_received=received.isoformat())
        if data["report_type"] == AE:
            data["event_onset_date"] = (received - timedelta(days=item["onset"])).isoformat()
        ref_id = repo.reserve_reference(operator, via="seed")
        repo.submit_report(ref_id, data, operator, via="seed")
        if item["status"] in {"In Review", "Pending", "Closed"}:
            workflow.advance(ref_id, "In Review", operator, via="seed")
        if item["status"] in {"Pending", "Closed"}:
            workflow.advance(ref_id, "Closed", operator, via="seed")      # opens the HITL gate
        if item["status"] == "Closed":
            workflow.approve_closure(ref_id, reviewer, attestation=True)
        refs.append(ref_id)
    return refs


def _remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        if target.exists():
            target.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the tracker with fictional reports")
    parser.add_argument("--db", type=Path, default=None, help="database path (default: data/tracker.db)")
    parser.add_argument("--reset", action="store_true", help="delete the database before seeding")
    args = parser.parse_args()
    path = args.db or config.db_path()
    if args.reset:
        _remove_db(path)
    repo = Repository(path)
    if repo.list_reports():
        print(f"{path.name} already has reports; use --reset to reseed.")
        return 0
    refs = seed(repo, WorkflowService(repo))
    print(f"Seeded {len(refs)} fictional reports into {path.name}: {refs[0]} .. {refs[-1]}")
    print("Status counts:", repo.status_counts())
    print("Audit chain:", repo.verify_audit_chain())
    return 0


if __name__ == "__main__":
    sys.exit(main())

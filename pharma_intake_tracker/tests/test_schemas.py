"""Schema validation: mandatory fields, formats, dates, AE conditionals, PII shield."""
from __future__ import annotations

from datetime import date

import pytest

from pharma_intake import config
from pharma_intake.errors import IntakeValidationError
from pharma_intake.pii import find_identifiers
from pharma_intake.schemas import IntakeReport, ReportType, validate_intake

from conftest import TODAY

CORE_FIELDS = [
    "report_type", "product_name", "batch_lot_number", "date_received",
    "reporter_type", "intake_channel", "description",
]


def issues_for(data, today=TODAY):
    with pytest.raises(IntakeValidationError) as exc:
        validate_intake(data, today=today)
    return {i.field: i for i in exc.value.issues}


def test_spec_lot_pattern_is_exact():
    assert config.LOT_PATTERN == r"^LOT-\d{5}$"


def test_valid_reports_pass(valid_pqc, valid_ae):
    pqc = validate_intake(valid_pqc(), today=TODAY)
    ae = validate_intake(valid_ae(), today=TODAY)
    assert pqc.report_type is ReportType.QUALITY_COMPLAINT and pqc.event_description is None
    assert ae.report_type is ReportType.ADVERSE_EVENT and ae.event_onset_date == date(2026, 11, 19)
    assert ae.to_record()["event_onset_date"] == "2026-11-19"


@pytest.mark.parametrize("field", CORE_FIELDS)
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_each_missing_mandatory_field_is_reported(valid_pqc, field, blank):
    found = issues_for(valid_pqc(**{field: blank}))
    assert list(found) == [field]
    assert found[field].code == "missing"
    assert "required" in found[field].message


def test_missing_messages_are_context_sensitive(valid_pqc, valid_ae):
    pqc_msg = issues_for(valid_pqc(batch_lot_number=None))["batch_lot_number"].message
    ae_msg = issues_for(valid_ae(batch_lot_number=None))["batch_lot_number"].message
    assert "quality complaint" in pqc_msg and "adverse event report" in ae_msg


def test_every_problem_is_reported_at_once():
    found = issues_for({"report_type": "Suspected Adverse Event"})
    assert set(found) == set(CORE_FIELDS[1:]) | {"event_description", "event_onset_date"}


@pytest.mark.parametrize("field", ["event_description", "event_onset_date"])
def test_ae_extension_fields_required_for_adverse_events(valid_ae, field):
    found = issues_for(valid_ae(**{field: None}))
    assert found[field].message.endswith("required for Suspected Adverse Event reports.")


def test_ae_fields_not_accepted_on_quality_complaints(valid_pqc):
    found = issues_for(valid_pqc(event_description="Something happened later on.", event_onset_date="2026-11-01"))
    assert {"event_description", "event_onset_date"} <= set(found)
    assert all(found[f].code == "not_applicable" for f in ("event_description", "event_onset_date"))


@pytest.mark.parametrize("lot", ["LOT-00001", "LOT-88392", "LOT-99999"])
def test_lot_format_accepts_spec_pattern(valid_pqc, lot):
    assert validate_intake(valid_pqc(batch_lot_number=lot), today=TODAY).batch_lot_number == lot


@pytest.mark.parametrize("lot", ["LOT-1234", "LOT-123456", "lot-12345", "LOT12345", "LOT-12A45", "LOT-１２３４５", "12345"])
def test_lot_format_rejects_everything_else(valid_pqc, lot):
    found = issues_for(valid_pqc(batch_lot_number=lot))
    assert "LOT-12345" in found["batch_lot_number"].message


def test_future_received_date_rejected(valid_pqc):
    found = issues_for(valid_pqc(date_received="2026-11-24"))
    assert "cannot be in the future" in found["date_received"].message


def test_onset_after_received_rejected(valid_ae):
    found = issues_for(valid_ae(event_onset_date="2026-11-22", date_received="2026-11-21"))
    assert found["event_onset_date"].code == "onset_after_received"


def test_onset_in_future_rejected(valid_ae):
    assert "future" in issues_for(valid_ae(event_onset_date="2026-12-01"))["event_onset_date"].message


def test_invalid_date_format_message(valid_pqc):
    assert "YYYY-MM-DD" in issues_for(valid_pqc(date_received="20/11/2026"))["date_received"].message


def test_short_description_message(valid_pqc):
    assert "at least 10 characters" in issues_for(valid_pqc(description="Broken"))["description"].message


def test_enum_values_enforced(valid_pqc):
    found = issues_for(valid_pqc(reporter_type="Dr Jones", intake_channel="Fax"))
    assert {"reporter_type", "intake_channel"} <= set(found)


def test_unknown_fields_rejected(valid_pqc):
    found = issues_for(valid_pqc(patient_name="anything"))
    assert "patient_name" in found


def test_model_is_safe_when_constructed_directly(valid_pqc):
    with pytest.raises(Exception):
        IntakeReport.model_validate(valid_pqc(report_type="Suspected Adverse Event"))


# ---------------------------------------------------------------- PII shield
PII_SAMPLES = {
    "email address": "Reporter can be reached at jane.doe@example.com for follow-up.",
    "phone number": "Caller left a callback number +44 20 7946 0958 for questions.",
    "national ID number": "Reporter quoted the number 123-45-6789 during the call.",
    "date of birth": "The consumer's DOB was given during the phone call.",
    "person name": "The patient name is recorded on the original email.",
    "street address": "The pack was returned from 42 Baker Street by courier.",
    "age": "A 45-year-old consumer reported the tablets were cracked.",
    "medical record number": "Hospital MRN was provided by the nurse.",
}


@pytest.mark.parametrize("kind,text", sorted(PII_SAMPLES.items()))
def test_identifier_like_text_is_rejected_without_echoing_it(valid_pqc, kind, text):
    found = issues_for(valid_pqc(description=text))
    message = found["description"].message
    assert kind in message
    assert "synthetic, de-identified" in message
    assert text not in message


@pytest.mark.parametrize(
    "text",
    [
        "Lot LOT-88392 received 2026-11-20 under REF-2026-0001; 50 mg tablets, 30 per pack.",
        "Pharmacist reported cracked tablets in blister 3 of 4.",
        "Event onset was 2026-11-18 at about 10:30 according to the reporter.",
    ],
)
def test_pii_guard_has_no_false_positive_on_normal_intake_text(text):
    assert find_identifiers(text) == []


def test_pii_applies_to_event_description_and_product(valid_ae):
    found = issues_for(valid_ae(event_description="Contact Mrs Smith for details.", product_name="info@x.org"))
    assert {"event_description", "product_name"} <= set(found)

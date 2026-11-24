"""Pydantic v2 schemas and context-sensitive validation for intake reports.

``validate_intake`` is the single entry point used by the UI and the agent
tools. It reports *every* problem at once, with a message specific to the
field and the report type, and never echoes free-text values back.
"""
from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from . import config
from .errors import FieldIssue, IntakeValidationError
from .pii import identifier_kinds


class ReportType(str, Enum):
    ADVERSE_EVENT = "Suspected Adverse Event"
    QUALITY_COMPLAINT = "Product Quality Complaint"


class Status(str, Enum):
    NEW = "New"
    IN_REVIEW = "In Review"
    CLOSED = "Closed"


class ReporterType(str, Enum):
    HEALTHCARE_PROFESSIONAL = "Healthcare Professional"
    PHARMACIST = "Pharmacist"
    CONSUMER = "Consumer"
    SALES_REPRESENTATIVE = "Sales Representative"
    OTHER = "Other"


class IntakeChannel(str, Enum):
    EMAIL = "Email"
    TELEPHONE = "Telephone"


LOT_RE = re.compile(config.LOT_PATTERN, re.ASCII)
REF_RE = re.compile(config.REF_PATTERN, re.ASCII)

INTAKE_FIELDS = (
    "report_type",
    "product_name",
    "batch_lot_number",
    "date_received",
    "reporter_type",
    "intake_channel",
    "description",
    "event_description",
    "event_onset_date",
)
AE_ONLY_FIELDS = ("event_description", "event_onset_date")
FREE_TEXT_FIELDS = ("product_name", "description", "event_description")

FIELD_LABELS: Dict[str, str] = {
    "report_type": "Report type",
    "product_name": "Product name",
    "batch_lot_number": "Batch/lot number",
    "date_received": "Date received",
    "reporter_type": "Reporter type",
    "intake_channel": "Intake channel",
    "description": "Description",
    "event_description": "Event description",
    "event_onset_date": "Event onset date",
}

_CONTEXT_NOUN = {
    ReportType.ADVERSE_EVENT: "adverse event report",
    ReportType.QUALITY_COMPLAINT: "quality complaint",
    None: "report",
}

_MISSING = {
    "report_type": "Report type is required — choose Suspected Adverse Event or Product Quality Complaint.",
    "product_name": "Product name is required — identify the fictional product this {noun} concerns.",
    "batch_lot_number": "Batch/lot number is required (format LOT-12345) so the {noun} can be traced to a batch.",
    "date_received": "Date received is required — enter the date the {noun} reached the company.",
    "reporter_type": "Reporter type is required — select the reporter category (never a name).",
    "intake_channel": "Intake channel is required — select Email or Telephone.",
    "description": "Description is required — summarise what was reported, without personal identifiers.",
    "event_description": "Event description is required for Suspected Adverse Event reports.",
    "event_onset_date": "Event onset date is required for Suspected Adverse Event reports.",
}


def _today(info: Optional[ValidationInfo]) -> date:
    context = (info.context if info is not None else None) or {}
    value = context.get("today")
    return value if isinstance(value, date) else date.today()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


class IntakeReport(BaseModel):
    """A validated intake report, ready to persist."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    report_type: ReportType
    product_name: str = Field(min_length=config.PRODUCT_NAME_MIN, max_length=config.PRODUCT_NAME_MAX)
    batch_lot_number: str
    date_received: date
    reporter_type: ReporterType
    intake_channel: IntakeChannel
    description: str = Field(min_length=config.DESCRIPTION_MIN, max_length=config.DESCRIPTION_MAX)
    event_description: Optional[str] = Field(
        default=None, min_length=config.DESCRIPTION_MIN, max_length=config.DESCRIPTION_MAX
    )
    event_onset_date: Optional[date] = None

    @model_validator(mode="before")
    @classmethod
    def _blank_values_are_missing(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if not _blank(v)}
        return data

    @field_validator("batch_lot_number")
    @classmethod
    def _lot_format(cls, value: str) -> str:
        if not LOT_RE.fullmatch(value):
            raise ValueError(
                "Batch/lot number must use the format LOT-12345 "
                "(LOT, a hyphen, then exactly 5 digits)."
            )
        return value

    @field_validator(*FREE_TEXT_FIELDS)
    @classmethod
    def _no_personal_identifiers(cls, value: Optional[str], info: ValidationInfo) -> Optional[str]:
        if value is None:
            return value
        kinds = identifier_kinds(value)
        if kinds:
            label = FIELD_LABELS[info.field_name or ""]
            raise ValueError(
                f"{label} appears to contain personal identifiers ({', '.join(kinds)}). "
                "Remove them — this prototype accepts synthetic, de-identified data only."
            )
        return value

    @field_validator("date_received", "event_onset_date")
    @classmethod
    def _not_in_future(cls, value: Optional[date], info: ValidationInfo) -> Optional[date]:
        if value is not None and value > _today(info):
            raise ValueError(f"{FIELD_LABELS[info.field_name or '']} cannot be in the future.")
        return value

    @model_validator(mode="after")
    def _type_specific_rules(self) -> "IntakeReport":
        # Mirrors _conditional_issues() so the model is safe even when built directly.
        if self.report_type is ReportType.ADVERSE_EVENT:
            if self.event_description is None or self.event_onset_date is None:
                raise ValueError("Event description and onset date are required for Suspected Adverse Event reports.")
            if self.event_onset_date > self.date_received:
                raise ValueError("Event onset date cannot be after the date received.")
        elif self.event_description is not None or self.event_onset_date is not None:
            raise ValueError("Event fields apply only to Suspected Adverse Event reports.")
        return self

    def to_record(self) -> Dict[str, Any]:
        """Plain dict with ISO dates and enum values, for persistence."""
        data = self.model_dump()
        out: Dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, date):
                out[key] = value.isoformat()
            else:
                out[key] = _enum_value(value)
        return out


class StoredReport(BaseModel):
    """A report as read back from the database (long-term memory)."""

    model_config = ConfigDict(frozen=True)

    ref_id: str
    report_type: ReportType
    product_name: str
    batch_lot_number: str
    date_received: date
    reporter_type: ReporterType
    intake_channel: IntakeChannel
    description: str
    event_description: Optional[str] = None
    event_onset_date: Optional[date] = None
    status: Status
    pending_closure: bool
    created_at: str
    created_by: str
    updated_at: str


# ---------------------------------------------------------------- validation entry point
def _parse_report_type(value: Any) -> Optional[ReportType]:
    try:
        return ReportType(_enum_value(value))
    except ValueError:
        return None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _message_for(error: Mapping[str, Any], field: str, noun: str) -> str:
    kind = error.get("type", "")
    label = FIELD_LABELS.get(field, field)
    ctx = error.get("ctx") or {}
    if kind == "missing":
        return _MISSING.get(field, f"{label} is required.").format(noun=noun)
    if kind == "string_too_short":
        if field == "description":
            return f"Description must be at least {ctx.get('min_length')} characters — summarise what was reported."
        return f"{label} must be at least {ctx.get('min_length')} characters."
    if kind == "string_too_long":
        return f"{label} must be at most {ctx.get('max_length')} characters."
    if kind == "enum":
        return f"{label} must be one of: {ctx.get('expected', 'the listed options')}."
    if kind.startswith("date") or kind.startswith("datetime"):
        return f"{label} must be a valid date (YYYY-MM-DD)."
    if kind == "value_error":
        return str(error.get("msg", "")).removeprefix("Value error, ")
    if kind == "extra_forbidden":
        return f"'{field}' is not a field of an intake report."
    if kind == "string_type":
        return f"{label} must be text."
    return f"{label} is invalid."


def _conditional_issues(data: Mapping[str, Any], today: date) -> List[FieldIssue]:
    """Rules that depend on the report type, checked even when other fields fail."""
    issues: List[FieldIssue] = []
    report_type = _parse_report_type(data.get("report_type"))
    if report_type is ReportType.ADVERSE_EVENT:
        for field in AE_ONLY_FIELDS:
            if _blank(data.get(field)):
                issues.append(FieldIssue(field, _MISSING[field], "missing"))
        onset = _parse_date(data.get("event_onset_date"))
        received = _parse_date(data.get("date_received"))
        if onset and received and onset > received and onset <= today:
            issues.append(
                FieldIssue(
                    "event_onset_date",
                    "Event onset date cannot be after the date received.",
                    "onset_after_received",
                )
            )
    elif report_type is ReportType.QUALITY_COMPLAINT:
        for field in AE_ONLY_FIELDS:
            if not _blank(data.get(field)):
                issues.append(
                    FieldIssue(
                        field,
                        f"{FIELD_LABELS[field]} applies only to Suspected Adverse Event reports — "
                        "clear it for a Product Quality Complaint.",
                        "not_applicable",
                    )
                )
    return issues


def validate_intake(data: Mapping[str, Any], today: Optional[date] = None) -> IntakeReport:
    """Validate raw form/tool input. Raises IntakeValidationError with every issue found."""
    today = today or date.today()
    clean = {k: v for k, v in dict(data).items() if not _blank(v)}
    noun = _CONTEXT_NOUN[_parse_report_type(clean.get("report_type"))]

    issues: List[FieldIssue] = []
    model: Optional[IntakeReport] = None
    model_level: List[FieldIssue] = []
    try:
        model = IntakeReport.model_validate(clean, context={"today": today})
    except ValidationError as exc:
        for error in exc.errors(include_url=False, include_input=False):
            loc = error.get("loc") or ()
            field = str(loc[0]) if loc else "report"
            issue = FieldIssue(field, _message_for(error, field, noun), str(error.get("type", "")))
            (issues if loc else model_level).append(issue)

    conditional = _conditional_issues(clean, today)
    seen = {(i.field, i.code) for i in issues}
    for issue in conditional:
        if (issue.field, issue.code) not in seen and issue.field not in {i.field for i in issues}:
            issues.append(issue)
    if not conditional:
        issues.extend(model_level)

    if issues:
        order = {name: idx for idx, name in enumerate(INTAKE_FIELDS)}
        issues.sort(key=lambda i: (order.get(i.field, len(order)), i.code))
        raise IntakeValidationError(issues)
    assert model is not None
    return model

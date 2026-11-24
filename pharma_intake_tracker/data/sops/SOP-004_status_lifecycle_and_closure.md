# SOP-004 Triage Status Lifecycle and Closure Approval

> FICTIONAL training document for the Pharma Safety Intake & Triage Tracker prototype. Not a real procedure.

## Status values
Every report has exactly one status: New, In Review or Closed. New means the report has been captured and is waiting for triage. In Review means a triage operator is actively working on it. Closed means the intake record is complete and a reviewer has approved its closure. Closed is final, and closed reports are read-only.

## Allowed transitions
The only allowed transitions are New to In Review and In Review to Closed. Skipping a step (New directly to Closed) or moving backwards (Closed to In Review, or In Review to New) is rejected by the tracker, which shows the allowed next status instead. Each accepted transition is recorded in the status history together with the operator ID and a timestamp.

## Human-in-the-loop closure approval
Moving a report from In Review to Closed does not close it straight away. It opens a closure request and freezes the report: while the request is pending, no further transitions are accepted. A designated human reviewer (a REV identifier) then opens the approvals queue, checks the record and either approves or rejects the request. Approval requires the reviewer to tick an attestation that the record is complete and was reviewed within the non-clinical scope of the tool. The approval is written to the audit trail before the report becomes Closed. The knowledge assistant can request a closure, but it can never approve one.

## Rejected closure requests
If the reviewer rejects a closure request, the report unfreezes and stays In Review. The reviewer selects a reason code: incomplete information, follow-up required, data quality or other. The triage operator resolves the issue and may request closure again later.

## Timeliness expectations
New reports should be moved to In Review within one business day of receipt. Reports that wait in New for longer are highlighted on the dashboard. Pending closure requests should be decided by a reviewer within two business days.

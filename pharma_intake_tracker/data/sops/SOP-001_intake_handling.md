# SOP-001 Intake Handling and Mandatory Fields

> FICTIONAL training document for the Pharma Safety Intake & Triage Tracker prototype. Not a real procedure.

## Purpose and scope
This procedure describes how intake operators capture product quality complaints and suspected adverse event reports that arrive by email or telephone. It covers report initiation, mandatory data, de-identification and hand-off to triage. Intake operators record information only. They do not assess causality, seriousness or regulatory reportability; qualified pharmacovigilance personnel perform those activities in the enterprise safety system.

## Intake channels
Reports arrive through two channels. Email reports are copied into the tracker by the operator on duty and the original message stays in the controlled mailbox. Telephone reports are typed into the tracker while the caller is on the line, reading the key fields back to the caller to confirm them. Operators select the intake channel (Email or Telephone) on every report so the source of the information is traceable.

## Report initiation and reference numbers
Starting a new report reserves a unique reference number in the format REF-YYYY-NNNN, for example REF-2026-0001. The reference is shown at the top of the intake form before any data is saved, so it can be quoted to a caller immediately. A reserved reference is never reused. If an intake is abandoned, the gap in the sequence is explained by the REFERENCE_RESERVED entry in the audit trail.

## Mandatory fields
Every report must contain the report type, the product name, the batch or lot number, the date received, the reporter type, the intake channel and a description of what was reported. Batch or lot numbers use the format LOT- followed by exactly five digits, such as LOT-40517. The date received cannot be in the future. When a mandatory field is missing, the form names the missing field and explains what to enter. The report cannot be saved until all errors are resolved.

## De-identification rules
The tracker holds synthetic, de-identified information only. Operators record the reporter type (for example Pharmacist or Consumer) and never the reporter's name. Do not enter names, initials, email addresses, phone numbers, street addresses, dates of birth, ages or medical record numbers. The intake form rejects free text that looks like a personal identifier and tells the operator which kind of identifier to remove.

## Hand-off to triage
Saved reports start in status New and appear on the dashboard. A triage operator moves the report to In Review when work on it begins. Closing a report always requires approval from a designated human reviewer as described in SOP-004.

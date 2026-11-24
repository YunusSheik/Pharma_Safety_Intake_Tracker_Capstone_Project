# SOP-005 Audit Trail and Data Minimization

> FICTIONAL training document for the Pharma Safety Intake & Triage Tracker prototype. Not a real procedure.

## Audit trail contents
The tracker keeps a local audit trail of every significant action: reference reservation, report creation, rejected submissions, status changes, closure requests, reviewer approvals and rejections, knowledge queries and control-plane events such as circuit breaker trips. Each entry records a UTC timestamp, the pseudonymous actor ID, the action, the reference number and a small set of structured details.

## What is never recorded
Audit entries never contain report descriptions, event narratives, chat questions or any personal details. For a rejected submission only the names of the fields that failed are kept, not the values typed. For a knowledge query only a short fingerprint of the question and its length are stored. Operators are identified only by IDs such as OP-001 and reviewers by IDs such as REV-01.

## Immutability and tamper evidence
The audit trail is append-only: the database refuses any attempt to change or delete an entry. Each entry also carries a cryptographic hash that chains it to the previous entry, so any change made outside the application breaks the chain. The audit view has a verification button that recalculates the whole chain and reports the first entry that does not match.

## Development governance log
Changes to the application's source files made with the AI coding assistant are recorded separately in logs/edit_audit.log by a deterministic hook. That log contains only the time, the tool used and the file path. It never contains file contents or report data.

## Retention in this prototype
Because the tracker is a training prototype with fictional data, the local database can be reset by deleting the data file and re-running the seed script. In a production system, retention periods, backups and access reviews would be defined by the quality and compliance functions.

# Checklist — Data minimization

Mark each item PASS or FAIL. For each FAIL, record the file and line.

## Collection
- [ ] The intake schema only collects these fields:
  - report type
  - product name
  - batch/lot
  - date received
  - reporter *type* (a category, never a name)
  - intake channel
  - description
  - for adverse events only: the event description and onset date
- [ ] No field asks for a patient name, initials, date of birth, age, sex, address, email, phone or medical history.
- [ ] Every free-text field (description, event description, product name) goes through `pii.find_identifiers` and is rejected on a hit.
- [ ] PII error messages name the field and the *kind* of identifier and never echo the matched value.

## Storage and logs
- [ ] `audit_log.details` only uses keys from `audit.ALLOWED_DETAIL_KEYS`. Unknown keys raise an error and are never silently stored.
- [ ] No audit action stores a description, event narrative, product free text or chat query. Queries are stored only as a hash and a length.
- [ ] The edit hook (`.claude/hooks/log_edit.py`) reads only `tool_name`, the path keys and `session_id`. It never touches `content`, `old_string`, `new_string` or `edits`.
- [ ] The hook writes project-relative paths, never absolute machine paths.
- [ ] Operators and reviewers are pseudonymous IDs from `config.OPERATORS` and `config.REVIEWERS`.

## Retrieval and agent
- [ ] RAG indexes report narratives only from the local DB, and nothing leaves the machine.
- [ ] The ReAct short-term memory is volatile (session only) and bounded (`config.SHORT_TERM_MEMORY_LIMIT`).
- [ ] Logs, `print` and Streamlit `st.write` calls never dump whole report objects in non-UI code paths.

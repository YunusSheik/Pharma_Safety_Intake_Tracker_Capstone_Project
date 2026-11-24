"""Append-only, hash-chained local audit trail.

Immutability is enforced two ways:
1. SQLite triggers abort any UPDATE or DELETE on audit_log (see db.py).
2. Each row stores SHA-256(canonical(row) + prev_hash), so any edit made
   outside SQLite (e.g. by patching the file) is caught by verify_chain().

Data minimization: ``details`` accepts only whitelisted keys holding short,
identifier-like values. Free text (descriptions, narratives, chat queries)
cannot be stored. Queries are recorded as a truncated hash plus a length.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from . import config
from .errors import AuditDetailError

GENESIS_HASH = "0" * 64


class AuditAction(str, Enum):
    REFERENCE_RESERVED = "REFERENCE_RESERVED"
    REPORT_CREATED = "REPORT_CREATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    STATUS_CHANGED = "STATUS_CHANGED"
    CLOSURE_REQUESTED = "CLOSURE_REQUESTED"
    CLOSURE_APPROVED = "CLOSURE_APPROVED"
    CLOSURE_REJECTED = "CLOSURE_REJECTED"
    RAG_QUERY = "RAG_QUERY"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"
    CIRCUIT_BREAKER_RESET = "CIRCUIT_BREAKER_RESET"
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"


ALLOWED_DETAIL_KEYS = frozenset(
    {
        "report_type",
        "status",
        "from_status",
        "to_status",
        "fields",
        "error_count",
        "result_count",
        "route",
        "tool",
        "reason",
        "decision",
        "attestation",
        "query_sha256",
        "query_length",
        "stage_counts",
        "mode",
        "signature",
        "attempts",
        "via",
    }
)
MAX_DETAIL_STRING = 64
_SAFE_STRING = re.compile(r"^[A-Za-z0-9 _\-./:]*$")
_ACTOR_RE = re.compile(config.ACTOR_PATTERN, re.ASCII)
_REF_RE = re.compile(config.REF_PATTERN, re.ASCII)


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: str
    actor: str
    action: str
    ref_id: Optional[str]
    details: Dict[str, Any]
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    first_bad_id: Optional[int] = None
    reason: str = ""


def _check_string(key: str, value: str) -> None:
    if len(value) > MAX_DETAIL_STRING or not _SAFE_STRING.fullmatch(value):
        raise AuditDetailError(
            f"audit detail '{key}' must be a short identifier-like value, not free text"
        )


def validate_details(details: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a clean copy of ``details`` or raise AuditDetailError."""
    if not details:
        return {}
    clean: Dict[str, Any] = {}
    for key, value in details.items():
        if key not in ALLOWED_DETAIL_KEYS:
            raise AuditDetailError(f"audit detail key '{key}' is not whitelisted")
        if isinstance(value, Enum):
            value = value.value
        if value is None or isinstance(value, (bool, int, float)):
            clean[key] = value
        elif isinstance(value, str):
            _check_string(key, value)
            clean[key] = value
        elif isinstance(value, (list, tuple)):
            items = []
            for item in value:
                item = item.value if isinstance(item, Enum) else item
                if not isinstance(item, str):
                    raise AuditDetailError(f"audit detail '{key}' list items must be strings")
                _check_string(key, item)
                items.append(item)
            clean[key] = items
        elif isinstance(value, Mapping):
            sub: Dict[str, Any] = {}
            for sub_key, sub_value in value.items():
                _check_string(key, str(sub_key))
                if not isinstance(sub_value, (int, float)) or isinstance(sub_value, bool):
                    raise AuditDetailError(f"audit detail '{key}' mapping values must be numbers")
                sub[str(sub_key)] = sub_value
            clean[key] = sub
        else:
            raise AuditDetailError(f"audit detail '{key}' has unsupported type {type(value).__name__}")
    return clean


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_hash(
    prev_hash: str, ts: str, actor: str, action: str, ref_id: Optional[str], details: Mapping[str, Any]
) -> str:
    body = canonical_json(
        {"ts": ts, "actor": actor, "action": action, "ref_id": ref_id, "details": dict(details)}
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def query_fingerprint(text: str) -> Dict[str, Any]:
    """What the audit log stores about a query: a truncated hash and the length, never the text."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    return {"query_sha256": digest, "query_length": len(text.strip())}


def append(
    conn: sqlite3.Connection,
    *,
    ts: str,
    actor: str,
    action: AuditAction,
    ref_id: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> AuditEntry:
    """Append one entry. Must run inside the caller's write transaction."""
    if not conn.in_transaction:
        raise AuditDetailError("audit.append must run inside a write transaction")
    if not _ACTOR_RE.fullmatch(actor or ""):
        raise AuditDetailError("audit actor must be a pseudonymous ID (OP-###, REV-##, SYSTEM or AGENT)")
    action = AuditAction(action)
    if ref_id is not None and not _REF_RE.fullmatch(ref_id):
        raise AuditDetailError("audit ref_id must be a REF-YYYY-NNNN identifier")
    clean = validate_details(details)
    row = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row[0] if row else GENESIS_HASH
    entry_hash = compute_hash(prev_hash, ts, actor, action.value, ref_id, clean)
    cursor = conn.execute(
        "INSERT INTO audit_log(ts, actor, action, ref_id, details, prev_hash, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, actor, action.value, ref_id, canonical_json(clean), prev_hash, entry_hash),
    )
    return AuditEntry(int(cursor.lastrowid or 0), ts, actor, action.value, ref_id, clean, prev_hash, entry_hash)


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=int(row["id"]),
        ts=row["ts"],
        actor=row["actor"],
        action=row["action"],
        ref_id=row["ref_id"],
        details=json.loads(row["details"]),
        prev_hash=row["prev_hash"],
        entry_hash=row["entry_hash"],
    )


def list_entries(
    conn: sqlite3.Connection, ref_id: Optional[str] = None, limit: Optional[int] = None
) -> List[AuditEntry]:
    sql = "SELECT * FROM audit_log"
    params: List[Any] = []
    if ref_id:
        sql += " WHERE ref_id = ?"
        params.append(ref_id)
    sql += " ORDER BY id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [_row_to_entry(r) for r in conn.execute(sql, params).fetchall()]


def verify_chain(conn: sqlite3.Connection) -> ChainReport:
    """Recompute every hash in order; report the first row that does not match."""
    prev = GENESIS_HASH
    checked = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY id ASC"):
        checked += 1
        if row["prev_hash"] != prev:
            return ChainReport(False, checked, int(row["id"]), "prev_hash does not link to the previous entry")
        try:
            details = json.loads(row["details"])
        except ValueError:
            return ChainReport(False, checked, int(row["id"]), "details are not valid JSON")
        expected = compute_hash(prev, row["ts"], row["actor"], row["action"], row["ref_id"], details)
        if row["entry_hash"] != expected:
            return ChainReport(False, checked, int(row["id"]), "entry_hash does not match the row content")
        prev = row["entry_hash"]
    return ChainReport(True, checked)

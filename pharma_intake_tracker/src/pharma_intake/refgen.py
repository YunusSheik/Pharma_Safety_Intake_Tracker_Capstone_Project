"""Unique reference identifiers: REF-<year>-<4-digit sequence>.

Sequences are allocated inside the caller's write transaction (BEGIN
IMMEDIATE), so two sessions cannot receive the same number.
"""
from __future__ import annotations

import re
import sqlite3

from . import config
from .errors import ReferenceError_

REF_RE = re.compile(config.REF_PATTERN, re.ASCII)


def format_reference(year: int, sequence: int) -> str:
    if not 1 <= sequence <= config.REF_MAX_SEQUENCE:
        raise ReferenceError_(f"Reference sequence for {year} is exhausted (max {config.REF_MAX_SEQUENCE}).")
    return f"{config.REF_PREFIX}-{year:04d}-{sequence:04d}"


def is_reference(value: str) -> bool:
    return bool(REF_RE.fullmatch(value or ""))


def next_reference(conn: sqlite3.Connection, year: int) -> str:
    """Allocate the next reference for ``year``. Must run inside a write transaction."""
    if not conn.in_transaction:
        raise ReferenceError_("next_reference must run inside a write transaction")
    conn.execute(
        "INSERT INTO ref_sequence(year, last_value) VALUES (?, 0) ON CONFLICT(year) DO NOTHING",
        (year,),
    )
    conn.execute("UPDATE ref_sequence SET last_value = last_value + 1 WHERE year = ?", (year,))
    (value,) = conn.execute("SELECT last_value FROM ref_sequence WHERE year = ?", (year,)).fetchone()
    return format_reference(year, int(value))

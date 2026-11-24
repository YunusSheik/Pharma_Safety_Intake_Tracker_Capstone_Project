"""PostToolUse governance hook: append one content-free line per file edit.

Registered in .claude/settings.json for Edit|Write|MultiEdit|NotebookEdit.

Deterministic by design:
- plain Python, no model call, no network, no randomness;
- reads only an explicit allowlist of payload fields (tool_name, the target path, session_id);
- never reads tool_input content, old_string/new_string, edits, or descriptions;
- the same payload always produces the same record (apart from the timestamp);
- never blocks the agent: every failure path exits 0.

Output: JSON Lines in <project>/logs/edit_audit.log
    {"event": "file_edit", "file_path": "pharma_intake_tracker/app.py",
     "session_id": "...", "tool_name": "Edit", "ts": "2026-11-23T12:00:00+00:00"}
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Optional

LOG_RELATIVE = Path("logs") / "edit_audit.log"
TRACKED_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
# The only keys ever read from tool_input. Content-bearing keys are never touched.
PATH_KEYS = ("file_path", "notebook_path")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def project_relative(raw_path: str, project_dir: Path) -> str:
    """Return a POSIX path relative to the project, or a redacted marker if outside it.

    Absolute machine paths are never written, so the log stays portable and
    does not leak user directory names.
    """
    if not raw_path:
        return ""
    try:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            return PurePath(raw_path).as_posix()
        rel = candidate.resolve().relative_to(project_dir.resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return "<outside-project>/" + PurePath(raw_path).name


def build_entry(
    payload: Dict[str, Any],
    project_dir: Path,
    now: Callable[[], datetime] = _utc_now,
) -> Optional[Dict[str, str]]:
    """Build the log record, or None if this event should not be logged."""
    tool_name = str(payload.get("tool_name", ""))
    if tool_name not in TRACKED_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    raw_path = ""
    if isinstance(tool_input, dict):
        for key in PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                raw_path = value
                break
    return {
        "event": "file_edit",
        "file_path": project_relative(raw_path, project_dir),
        "session_id": str(payload.get("session_id", "")),
        "tool_name": tool_name,
        "ts": now().isoformat(timespec="seconds"),
    }


def append_entry(entry: Dict[str, str], project_dir: Path) -> Path:
    log_path = project_dir / LOG_RELATIVE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return log_path


def resolve_project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def main(stdin: Any = None) -> int:
    stream = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(stream)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        project_dir = resolve_project_dir()
        entry = build_entry(payload, project_dir)
        if entry is not None:
            append_entry(entry, project_dir)
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

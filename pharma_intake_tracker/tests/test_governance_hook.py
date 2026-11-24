"""Phase V: the PostToolUse hook is registered, deterministic and content-free."""
from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "log_edit.py"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
SECRET = "PATIENT-NARRATIVE-SHOULD-NEVER-BE-LOGGED"
ALLOWED_KEYS = {"event", "file_path", "session_id", "tool_name", "ts"}


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("log_edit", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fixed_now():
    return datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def test_settings_register_post_tool_use_hook():
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    entries = settings["hooks"]["PostToolUse"]
    matcher = entries[0]["matcher"]
    assert all(tool in matcher for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"))
    command = entries[0]["hooks"][0]["command"]
    assert entries[0]["hooks"][0]["type"] == "command" and ".claude/hooks/log_edit.py" in command


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Write", {"file_path": "{root}/pharma_intake_tracker/app.py", "content": SECRET}),
        ("Edit", {"file_path": "{root}/CLAUDE.md", "old_string": SECRET, "new_string": SECRET}),
        ("MultiEdit", {"file_path": "{root}/docs/PLAN.md", "edits": [{"old_string": SECRET, "new_string": "x"}]}),
        ("NotebookEdit", {"notebook_path": "{root}/nb.ipynb", "new_source": SECRET}),
    ],
)
def test_entry_contains_no_content(hook, tmp_path, tool, tool_input):
    tool_input = {k: (v.format(root=tmp_path) if isinstance(v, str) else v) for k, v in tool_input.items()}
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "s-1",
               "tool_response": {"content": SECRET}, "description": SECRET}
    entry = hook.build_entry(payload, tmp_path, now=fixed_now)
    assert set(entry) == ALLOWED_KEYS
    assert SECRET not in json.dumps(entry)
    assert not Path(entry["file_path"]).is_absolute()


def test_paths_are_project_relative_or_redacted(hook, tmp_path):
    inside = hook.build_entry({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a" / "b.py")}},
                              tmp_path, now=fixed_now)
    outside = hook.build_entry({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path.parent / "x.txt")}},
                               tmp_path, now=fixed_now)
    assert inside["file_path"] == "a/b.py"
    assert outside["file_path"] == "<outside-project>/x.txt"


def test_untracked_tools_are_ignored(hook, tmp_path):
    assert hook.build_entry({"tool_name": "Bash", "tool_input": {"command": "ls"}}, tmp_path) is None


def test_deterministic_output(hook, tmp_path):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "f.py")}, "session_id": "s"}
    assert hook.build_entry(payload, tmp_path, now=fixed_now) == hook.build_entry(payload, tmp_path, now=fixed_now)


def test_main_appends_one_line_and_never_fails(hook, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "app.py"), "content": SECRET}}
    assert hook.main(io.StringIO(json.dumps(payload))) == 0
    assert hook.main(io.StringIO("not json")) == 0
    assert hook.main(io.StringIO("[1, 2]")) == 0
    lines = (tmp_path / "logs" / "edit_audit.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["file_path"] == "app.py" and SECRET not in lines[0]


def test_committed_execution_log_is_content_free():
    log = REPO_ROOT / "logs" / "edit_audit.log"
    if not log.exists():
        pytest.skip("no execution log yet")
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines
    for line in lines:
        entry = json.loads(line)
        assert set(entry) == ALLOWED_KEYS and entry["event"] == "file_edit"
        assert not Path(entry["file_path"]).is_absolute() and ":\\" not in entry["file_path"]

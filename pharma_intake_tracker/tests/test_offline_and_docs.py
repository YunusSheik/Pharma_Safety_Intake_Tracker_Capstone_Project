"""Strictly-local guarantees and presence of the governance deliverables."""
from __future__ import annotations

import ast
import os
import re
import socket
from pathlib import Path

import pytest

from pharma_intake import config

APP_ROOT = config.APP_ROOT
REPO_ROOT = APP_ROOT.parent
SRC = APP_ROOT / "src" / "pharma_intake"
NETWORK_MODULES = {"requests", "httpx", "aiohttp", "urllib3", "socket", "http.client", "websocket"}
KEY_SHAPES = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|\bghp_[A-Za-z0-9]{30,}")
SKIP_DIRS = {".venv", "models", ".git", "__pycache__", ".pytest_cache", "logs"}


def test_socket_guard_blocks_external_hosts():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="offline guard"):
            sock.connect(("93.184.216.34", 80))


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_no_network_libraries_in_application_code():
    files = list(SRC.rglob("*.py")) + [APP_ROOT / "app.py"]
    offenders = {f.name: m for f in files for m in _imports(f) if m in NETWORK_MODULES or m.split(".")[0] in {"requests", "httpx", "aiohttp"}}
    assert offenders == {}
    urllib_users = {f.relative_to(SRC).as_posix() for f in files if any(m.startswith("urllib.request") for m in _imports(f))}
    assert urllib_users == {"rag/generator.py"}          # loopback-only Ollama adapter


def test_model_loading_forces_offline(monkeypatch):
    from pharma_intake.rag.dense import _force_offline

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    _force_offline()
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_no_secrets_or_machine_paths_committed():
    hits = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".txt", ".ini", ".yml", ".yaml", ".cfg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if KEY_SHAPES.search(text):
            hits.append(f"secret-like token in {path.name}")
        if re.search(r"[A-Za-z]:\\Users\\", text):
            hits.append(f"absolute Windows user path in {path.name}")
    assert hits == []


@pytest.mark.parametrize(
    "relative",
    [
        "CLAUDE.md", "README.md", "LICENSE", ".gitignore",
        "docs/PLAN.md", "docs/ACCEPTANCE.md", "docs/DEFENSE.md", "docs/DEMO.md", "docs/DEMO_OUTPUT.md",
        "docs/review-evidence.md", "docs/WORKFLOW.md",
        ".claude/settings.json", ".claude/hooks/log_edit.py",
        ".claude/skills/pharma-intake-review/SKILL.md",
        ".claude/skills/pharma-intake-review/checklists/data-minimization.md",
        ".claude/skills/pharma-intake-review/checklists/error-handling.md",
        ".claude/skills/pharma-intake-review/checklists/auditability.md",
        ".claude/skills/pharma-intake-review/checklists/test-coverage.md",
        ".github/workflows/ci.yml",
    ],
)
def test_deliverable_files_exist(relative):
    assert (REPO_ROOT / relative).is_file(), relative


def test_claude_md_covers_required_topics():
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").lower()
    for topic in ("scope", "non-clinical", "synthetic", "architecture", "coding guidelines", "testing", "fictional"):
        assert topic in text, topic


def test_skill_frontmatter_and_checklists():
    text = (REPO_ROOT / ".claude/skills/pharma-intake-review/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: pharma-intake-review\n")
    for name in ("data-minimization", "error-handling", "auditability", "test-coverage"):
        assert f"checklists/{name}.md" in text


def test_gitignore_excludes_runtime_artifacts():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", "__pycache__/", "*.db", "models/", ".env", "settings.local.json"):
        assert pattern in text, pattern

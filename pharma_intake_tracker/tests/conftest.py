"""Shared fixtures. A socket guard blocks every non-loopback connection during the whole run."""
from __future__ import annotations

import ipaddress
import os
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

import pytest

from pharma_intake import config
from pharma_intake.rag.dense import model_present
from pharma_intake.repository import Repository
from pharma_intake.workflow import WorkflowService

# ---------------------------------------------------------------- offline guard
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
BLOCKED_ATTEMPTS: List[str] = []


def _is_loopback(host: Any) -> bool:
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _check(address: Any) -> None:
    if isinstance(address, (str, bytes)):          # AF_UNIX: local by definition
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if not _is_loopback(host):
        BLOCKED_ATTEMPTS.append(str(host))
        raise ConnectionRefusedError(f"Network access blocked by the test offline guard: {host!r}")


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    _check(address)
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    _check(address)
    return _REAL_CONNECT_EX(self, address)


@pytest.fixture(scope="session", autouse=True)
def offline_guard() -> Iterator[None]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    socket.socket.connect = _guarded_connect          # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex    # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = _REAL_CONNECT          # type: ignore[method-assign]
        socket.socket.connect_ex = _REAL_CONNECT_EX    # type: ignore[method-assign]


# ---------------------------------------------------------------- clock + stores
class TickClock:
    """Deterministic clock: 2026-11-23T09:00:00Z, advancing one second per call."""

    def __init__(self, start: datetime = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)) -> None:
        self.current = start

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


TODAY = date(2026, 11, 23)


@pytest.fixture
def clock() -> TickClock:
    return TickClock()


@pytest.fixture
def repo(tmp_path: Path, clock: TickClock) -> Repository:
    return Repository(tmp_path / "tracker_test.db", clock=clock)


@pytest.fixture
def workflow(repo: Repository) -> WorkflowService:
    return WorkflowService(repo)


@pytest.fixture
def seeded(repo: Repository, workflow: WorkflowService) -> List[str]:
    from scripts.seed import seed

    return seed(repo, workflow)


# ---------------------------------------------------------------- payloads
@pytest.fixture
def valid_pqc() -> Callable[..., Dict[str, Any]]:
    def make(**overrides: Any) -> Dict[str, Any]:
        data = {
            "report_type": "Product Quality Complaint",
            "product_name": "Zentravol 50 mg tablets",
            "batch_lot_number": "LOT-40517",
            "date_received": "2026-11-20",
            "reporter_type": "Pharmacist",
            "intake_channel": "Email",
            "description": "Several tablets in a sealed blister were cracked on opening.",
        }
        data.update(overrides)
        return data

    return make


@pytest.fixture
def valid_ae() -> Callable[..., Dict[str, Any]]:
    def make(**overrides: Any) -> Dict[str, Any]:
        data = {
            "report_type": "Suspected Adverse Event",
            "product_name": "Clarivex 10 mg/mL oral solution",
            "batch_lot_number": "LOT-51207",
            "date_received": "2026-11-21",
            "reporter_type": "Healthcare Professional",
            "intake_channel": "Telephone",
            "description": "Healthcare professional reported itching on both arms after a dose.",
            "event_description": "Pruritus on both arms, reported as mild.",
            "event_onset_date": "2026-11-19",
        }
        data.update(overrides)
        return data

    return make


@pytest.fixture
def make_report(repo: Repository, valid_pqc: Callable[..., Dict[str, Any]]) -> Callable[..., str]:
    def make(actor: str = "OP-001", **overrides: Any) -> str:
        ref_id = repo.reserve_reference(actor)
        repo.submit_report(ref_id, valid_pqc(**overrides), actor)
        return ref_id

    return make


# ---------------------------------------------------------------- RAG
REAL_MODELS_AVAILABLE = (
    config.rag_mode() != "fallback"
    and model_present(config.BI_ENCODER_DIRNAME)
    and model_present(config.CROSS_ENCODER_DIRNAME)
)


def pytest_collection_modifyitems(config: Any, items: List[Any]) -> None:
    if REAL_MODELS_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="real local models not available (run scripts/download_models.py) "
                                   "or PHARMA_RAG_MODE=fallback")
    for item in items:
        if "models" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fallback_pipeline(repo: Repository) -> Any:
    from pharma_intake.rag.pipeline import build_pipeline

    return build_pipeline(repo.list_reports(), mode="fallback")


@pytest.fixture(scope="session")
def real_pipeline_factory() -> Any:
    from pharma_intake.rag.pipeline import build_pipeline

    return lambda reports: build_pipeline(reports, mode="real")

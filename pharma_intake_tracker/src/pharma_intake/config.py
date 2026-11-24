"""Single source of truth for paths, spec parameters and limits.

Tests assert these values against the case-study specification, so change
them here only, and update the tests and docs/ACCEPTANCE.md with them.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- paths
PACKAGE_DIR = Path(__file__).resolve().parent          # src/pharma_intake
APP_ROOT = PACKAGE_DIR.parents[1]                      # pharma_intake_tracker/
DATA_DIR = APP_ROOT / "data"
SOP_DIR = DATA_DIR / "sops"
DEFAULT_DB_PATH = DATA_DIR / "tracker.db"
DEFAULT_MODELS_DIR = APP_ROOT / "models"


def db_path() -> Path:
    return Path(os.environ.get("PHARMA_DB_PATH", str(DEFAULT_DB_PATH)))


def models_dir() -> Path:
    return Path(os.environ.get("PHARMA_MODELS_DIR", str(DEFAULT_MODELS_DIR)))


def utc_now() -> datetime:
    """The single clock used for audit timestamps (injectable in tests)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- identity (pseudonymous only)
OPERATORS = ("OP-001", "OP-002", "OP-003")
REVIEWERS = ("REV-01", "REV-02")
SYSTEM_ACTORS = ("SYSTEM", "AGENT")
ACTOR_PATTERN = r"^(OP-\d{3}|REV-\d{2}|SYSTEM|AGENT)$"

# ---------------------------------------------------------------- reference ids
REF_PREFIX = "REF"
REF_PATTERN = r"^REF-\d{4}-\d{4}$"
REF_MAX_SEQUENCE = 9999

# ---------------------------------------------------------------- validation
LOT_PATTERN = r"^LOT-\d{5}$"          # spec: batch_id format ^LOT-\d{5}$
DESCRIPTION_MIN = 10
DESCRIPTION_MAX = 2000
PRODUCT_NAME_MIN = 2
PRODUCT_NAME_MAX = 120

# ---------------------------------------------------------------- RAG (spec section 5)
BM25_K1 = 1.5                         # spec: 1.2 – 1.5
BM25_K1_RANGE = (1.2, 1.5)
BM25_B = 0.75                         # spec: 0.75
CHUNK_TOKENS = 512                    # spec: 512-token chunks
CHUNK_OVERLAP = 64                    # spec: 64-token overlap
BM25_TOP_K = 50                       # spec: top-50 from BM25
DENSE_TOP_K = 50                      # spec: top-50 from bi-encoder
RRF_K = 60                            # spec: k = 60
RERANK_THRESHOLD = 0.40               # spec: drop below 0.40
RERANK_TOP_K = 5                      # spec: pass top-3 to top-5 to the prompt
RERANK_TOP_K_RANGE = (3, 5)

BI_ENCODER_MODEL = "BAAI/bge-small-en-v1.5"
# Chosen over cross-encoder/ms-marco-MiniLM-L-6-v2 after evaluation: ms-marco scored ~0.0 on
# lay-term -> clinical-term pairs ("difficulty breathing" vs "dyspnea"), so Stage 4 dropped every
# correct semantic match. See docs/PLAN.md "Deviations".
CROSS_ENCODER_MODEL = "mixedbread-ai/mxbai-rerank-xsmall-v1"
BI_ENCODER_DIRNAME = "bge-small-en-v1.5"
CROSS_ENCODER_DIRNAME = "mxbai-rerank-xsmall-v1"
BI_ENCODER_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
FALLBACK_EMBED_DIM = 768


def rag_mode() -> str:
    """'auto' (real models if downloaded, else fallback), 'real' (required) or 'fallback'."""
    value = os.environ.get("PHARMA_RAG_MODE", "auto").strip().lower()
    if value not in {"auto", "real", "fallback"}:
        raise ValueError("PHARMA_RAG_MODE must be one of: auto, real, fallback")
    return value


# ---------------------------------------------------------------- generation
def llm_backend() -> str:
    """'extractive' (default, deterministic) or 'ollama' (local loopback server only)."""
    value = os.environ.get("PHARMA_LLM_BACKEND", "extractive").strip().lower()
    if value not in {"extractive", "ollama"}:
        raise ValueError("PHARMA_LLM_BACKEND must be 'extractive' or 'ollama'")
    return value


OLLAMA_URL = os.environ.get("PHARMA_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("PHARMA_OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT_S = 60

# ---------------------------------------------------------------- control plane (spec section 6)
BREAKER_MAX_CONSECUTIVE_INVALID = 2   # trips when the same invalid call repeats MORE than 2 times
SHORT_TERM_MEMORY_LIMIT = 60          # ReAct steps kept per chat session (volatile)
ROUTER_SEMANTIC_THRESHOLD = 0.45      # fixed threshold for the exemplar-similarity tier

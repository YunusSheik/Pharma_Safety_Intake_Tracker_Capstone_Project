"""Answer generation over the reranked context.

Default ``extractive``: deterministic. It quotes the most query-relevant
sentences from each surviving chunk with source tags, so every sentence in
the answer exists verbatim in the corpus (no hallucination is possible).

Optional ``ollama``: a local LLM served on a loopback address only. Any other
host raises OfflineViolation. If Ollama is unreachable, the generator falls
back to the extractive answer.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence
from urllib.parse import urlparse

from .. import config
from ..errors import OfflineViolation
from .corpus import Chunk
from .prompt import source_tag
from .text import split_sentences, tokenize

NO_ANSWER = (
    "No sufficiently relevant record found in the local SOPs or past reports. "
    "Try rephrasing, or check the reference or lot number."
)


@dataclass
class Answer:
    text: str
    citations: List[str] = field(default_factory=list)
    backend: str = "extractive"
    note: str = ""


class Generator(Protocol):
    name: str

    def generate(self, query: str, chunks: Sequence[Chunk], prompt: str) -> Answer: ...


def _citations(chunks: Sequence[Chunk]) -> List[str]:
    return [f"[{source_tag(i)}] {c.citation}" for i, c in enumerate(chunks, start=1)]


class ExtractiveGenerator:
    name = "extractive"

    def __init__(self, sentences_per_chunk: int = 2) -> None:
        self.sentences_per_chunk = sentences_per_chunk

    def _best_sentences(self, query_terms: set, text: str) -> List[str]:
        body = text.split("\n", 1)[1] if "\n" in text else text   # drop the "Title — Section" header line
        sentences = split_sentences(" ".join(body.split()))
        if not sentences:
            return []
        ranked = sorted(
            range(len(sentences)),
            key=lambda i: (-len(query_terms & set(tokenize(sentences[i]))), i),
        )
        chosen = sorted(ranked[: self.sentences_per_chunk])
        return [sentences[i] for i in chosen]

    def generate(self, query: str, chunks: Sequence[Chunk], prompt: str) -> Answer:
        if not chunks:
            return Answer(NO_ANSWER, [], self.name)
        query_terms = set(tokenize(query))
        lines = ["Relevant guidance and records (quoted from local sources):"]
        for i, chunk in enumerate(chunks, start=1):
            quote = " ".join(self._best_sentences(query_terms, chunk.text))
            if quote:
                lines.append(f"- {quote} [{source_tag(i)}]")
        return Answer("\n".join(lines), _citations(chunks), self.name)


def ensure_loopback(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return url
    try:
        if ipaddress.ip_address(host).is_loopback:
            return url
    except ValueError:
        pass
    raise OfflineViolation("Only a loopback Ollama URL (127.0.0.1 / ::1 / localhost) is permitted")


class OllamaGenerator:
    def __init__(self, url: str = config.OLLAMA_URL, model: str = config.OLLAMA_MODEL,
                 timeout: float = config.OLLAMA_TIMEOUT_S) -> None:
        self.url = ensure_loopback(url).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = f"ollama:{model}"
        self._fallback = ExtractiveGenerator()

    def generate(self, query: str, chunks: Sequence[Chunk], prompt: str) -> Answer:
        if not chunks:
            return Answer(NO_ANSWER, [], self.name)
        body = json.dumps(
            {"model": self.model, "prompt": prompt, "stream": False,
             "options": {"temperature": 0, "seed": 7}}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # loopback only
                text = str(json.loads(response.read().decode("utf-8")).get("response", "")).strip()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            answer = self._fallback.generate(query, chunks, prompt)
            answer.note = f"Local Ollama unavailable ({type(exc).__name__}); used extractive answer."
            return answer
        return Answer(text or NO_ANSWER, _citations(chunks), self.name)


def load_generator(backend: Optional[str] = None) -> Generator:
    backend = backend or config.llm_backend()
    if backend == "ollama":
        return OllamaGenerator()
    return ExtractiveGenerator()

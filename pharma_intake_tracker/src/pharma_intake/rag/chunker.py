"""Token-window chunking: 512-token chunks with a 64-token overlap (spec, Stage 2).

Tokens are counted with the bi-encoder's own tokenizer when it is loaded, so
"512 tokens" means 512 model tokens. Otherwise a regex sub-word counter is
used. Windows are cut on token boundaries and mapped back to character
offsets, so chunk text is always an exact substring of the source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Protocol, Sequence, Tuple

from .. import config
from .text import regex_spans


class TokenCounter(Protocol):
    name: str

    def spans(self, text: str) -> List[Tuple[int, int]]: ...


class RegexTokenCounter:
    name = "regex"

    def spans(self, text: str) -> List[Tuple[int, int]]:
        return regex_spans(text)


class HFTokenCounter:
    """Uses a Hugging Face fast tokenizer's offset mapping (no special tokens)."""

    name = "model-tokenizer"

    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer

    def spans(self, text: str) -> List[Tuple[int, int]]:
        encoded = self._tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, truncation=False, verbose=False
        )
        return [(int(s), int(e)) for s, e in encoded["offset_mapping"] if e > s]


@dataclass(frozen=True)
class Window:
    text: str
    token_count: int
    start_token: int


def split_windows(
    text: str,
    counter: TokenCounter,
    size: int = config.CHUNK_TOKENS,
    overlap: int = config.CHUNK_OVERLAP,
) -> List[Window]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("chunk size must be positive and overlap must be in [0, size)")
    spans: Sequence[Tuple[int, int]] = counter.spans(text)
    if not spans:
        return []
    step = size - overlap
    windows: List[Window] = []
    start = 0
    while start < len(spans):
        end = min(start + size, len(spans))
        windows.append(Window(text[spans[start][0] : spans[end - 1][1]], end - start, start))
        if end == len(spans):
            break
        start += step
    return windows

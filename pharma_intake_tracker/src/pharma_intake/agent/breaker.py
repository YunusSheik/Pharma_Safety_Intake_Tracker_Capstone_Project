"""Circuit breaker (control plane, step 5).

Tracks the signature of each tool call: the tool name plus the SHA-256 of its
canonical JSON arguments, as the agent sent them, before any auto-repair. If the
exact same tool is called with the same invalid arguments MORE than
``max_consecutive_invalid`` (= 2) times in a row, the breaker opens and all
further tool execution is halted until a human resets it. This stops infinite
retry loops. A valid call, or an invalid call with a different signature,
resets the streak.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .. import config
from ..errors import CircuitOpenError


def call_signature(tool_name: str, args: Mapping[str, Any]) -> str:
    body = json.dumps({"tool": tool_name, "args": dict(args)}, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BreakerState:
    is_open: bool
    consecutive_invalid: int
    last_signature: Optional[str]
    tripped_now: bool = False


class CircuitBreaker:
    def __init__(self, max_consecutive_invalid: int = config.BREAKER_MAX_CONSECUTIVE_INVALID) -> None:
        if max_consecutive_invalid < 1:
            raise ValueError("max_consecutive_invalid must be >= 1")
        self.max_consecutive_invalid = max_consecutive_invalid
        self._open = False
        self._count = 0
        self._last: Optional[str] = None
        self.trip_count = 0

    @property
    def is_open(self) -> bool:
        return self._open

    def state(self, tripped_now: bool = False) -> BreakerState:
        return BreakerState(self._open, self._count, self._last, tripped_now)

    def check(self) -> None:
        if self._open:
            raise CircuitOpenError(
                "Circuit breaker is OPEN: the same invalid tool call was repeated more than "
                f"{self.max_consecutive_invalid} times. Execution is halted until a human resets the agent."
            )

    def record_success(self) -> BreakerState:
        self._count = 0
        self._last = None
        return self.state()

    def record_failure(self, signature: str) -> BreakerState:
        if signature == self._last:
            self._count += 1
        else:
            self._last = signature
            self._count = 1
        if self._count > self.max_consecutive_invalid and not self._open:
            self._open = True
            self.trip_count += 1
            return self.state(tripped_now=True)
        return self.state()

    def reset(self) -> BreakerState:
        self._open = False
        self._count = 0
        self._last = None
        return self.state()

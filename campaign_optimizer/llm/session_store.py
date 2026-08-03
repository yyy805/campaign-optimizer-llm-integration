"""Server-owned, isolated chat history storage boundary."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

class SessionStoreError(RuntimeError):
    """Safe persistence-boundary failure exposed to orchestration."""

@dataclass(frozen=True)
class SessionContext:
    tenant_id: str
    user_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.tenant_id, self.user_id, self.session_id)
        ):
            raise ValueError("session identity fields must be non-empty strings")


@dataclass(frozen=True)
class SessionBinding:
    tenant_id: str
    user_id: str
    session_id: str
    plan_id: str
    review_id: str
    context_id: str


@dataclass(frozen=True)
class SessionSnapshot:
    history: tuple[dict[str, str], ...]
    revision: int


class SessionStore(Protocol):
    def read(self, binding: SessionBinding) -> SessionSnapshot: ...

    def append_exchange(
        self, binding: SessionBinding, *, question: str, answer: str
    ) -> SessionSnapshot: ...


@dataclass
class _Record:
    history: list[dict[str, str]]
    revision: int
    expires_at: float


class InMemorySessionStore:
    """Lock-serialized local store with lazy TTL expiry and exact binding keys."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30 * 60,
        max_messages: int = 10,
        max_chars: int = 8_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("session TTL must be positive")
        if max_messages < 2 or max_messages > 10 or max_messages % 2:
            raise ValueError("session max_messages must be an even number from 2 to 10")
        if max_chars < 2:
            raise ValueError("session max_chars must preserve a user/assistant pair")
        self._ttl_seconds = ttl_seconds
        self._max_messages = max_messages
        self._max_chars = max_chars
        self._clock = clock
        self._records: dict[SessionBinding, _Record] = {}
        self._lock = threading.RLock()

    def read(self, binding: SessionBinding) -> SessionSnapshot:
        with self._lock:
            record = self._live_record(binding)
            if record is None:
                return SessionSnapshot((), 0)
            return _snapshot(record)

    def append_exchange(
        self, binding: SessionBinding, *, question: str, answer: str
    ) -> SessionSnapshot:
        if not isinstance(question, str) or not question:
            raise ValueError("session question must be non-empty")
        if not isinstance(answer, str) or not answer:
            raise ValueError("session answer must be non-empty")
        with self._lock:
            record = self._live_record(binding)
            history = [] if record is None else list(record.history)
            history.extend(
                (
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                )
            )
            history = _trim_exchanges(
                history, max_messages=self._max_messages, max_chars=self._max_chars
            )
            revision = 1 if record is None else record.revision + 1
            record = _Record(
                history=history,
                revision=revision,
                expires_at=self._clock() + self._ttl_seconds,
            )
            self._records[binding] = record
            return _snapshot(record)

    def _live_record(self, binding: SessionBinding) -> _Record | None:
        record = self._records.get(binding)
        if record is not None and record.expires_at <= self._clock():
            del self._records[binding]
            return None
        return record


def _snapshot(record: _Record) -> SessionSnapshot:
    return SessionSnapshot(
        tuple(dict(message) for message in record.history), record.revision
    )

def _trim_exchanges(
    history: list[dict[str, str]], *, max_messages: int, max_chars: int
) -> list[dict[str, str]]:
    if len(history) % 2:
        raise ValueError("server history must contain complete exchanges")
    pairs = [history[index : index + 2] for index in range(0, len(history), 2)]
    kept: list[list[dict[str, str]]] = []
    remaining = max_chars
    for pair in reversed(pairs):
        if len(kept) * 2 >= max_messages or remaining < 2:
            break
        user, assistant = pair
        if user["role"] != "user" or assistant["role"] != "assistant":
            raise SessionStoreError("server history roles must alternate by exchange")
        user_content = user["content"][: min(2_000, remaining - 1)]
        assistant_content = assistant["content"][: min(2_000, remaining - len(user_content))]
        if not user_content or not assistant_content:
            break
        kept.append(
            [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": assistant_content},
            ]
        )
        remaining -= len(user_content) + len(assistant_content)
    kept.reverse()
    return [message for pair in kept for message in pair]

"""Immutable, safe metadata envelope around validated workflow output."""
from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .qwen_client import QwenUsage


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _usage_dict(usage: QwenUsage | None) -> dict[str, int | None] | None:
    if usage is None:
        return None
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}


@dataclass(frozen=True)
class AttemptMetadata:
    attempt_number: int
    phase: str
    status: str
    provider: str
    model: str | None = None
    request_id: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    usage: QwenUsage | None = None
    finish_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_number": self.attempt_number, "phase": self.phase,
            "status": self.status, "provider": self.provider, "model": self.model,
            "request_id": self.request_id, "status_code": self.status_code,
            "latency_ms": self.latency_ms, "usage": _usage_dict(self.usage),
            "finish_reason": self.finish_reason,
        }


@dataclass(frozen=True)
class OrchestrationResult(Mapping[str, Any]):
    output: Mapping[str, Any]
    mode: str
    routed_intent: str
    router_source: str
    router_confidence: float
    provider: str
    attempts: tuple[AttemptMetadata, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None
    refusal_reason: str | None = None
    persistence_status: str = "NOT_APPLICABLE"
    revision_rounds: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _freeze(self.output))
        object.__setattr__(self, "attempts", tuple(self.attempts))

    def __getitem__(self, key: str) -> Any:
        return self.output[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.output)

    def __len__(self) -> int:
        return len(self.output)

    @property
    def provider_attempts(self) -> int:
        return len(self.attempts)

    @property
    def repair_attempts(self) -> int:
        return sum(attempt.phase == "repair" for attempt in self.attempts)

    @property
    def model(self) -> str | None:
        return self.attempts[-1].model if self.attempts else None

    @property
    def request_id(self) -> str | None:
        return self.attempts[-1].request_id if self.attempts else None

    @property
    def latency_ms(self) -> float | None:
        return self.attempts[-1].latency_ms if self.attempts else None

    @property
    def usage(self) -> QwenUsage | None:
        return self.attempts[-1].usage if self.attempts else None

    @property
    def finish_reason(self) -> str | None:
        return self.attempts[-1].finish_reason if self.attempts else None

    @property
    def total_latency_ms(self) -> float | None:
        values = [attempt.latency_ms for attempt in self.attempts if attempt.latency_ms is not None]
        return sum(values) if values else None

    @property
    def total_usage(self) -> QwenUsage | None:
        usages = [attempt.usage for attempt in self.attempts if attempt.usage is not None]
        if not usages:
            return None
        def total(name: str) -> int | None:
            values = [getattr(usage, name) for usage in usages]
            known = [value for value in values if value is not None]
            return sum(known) if known else None
        return QwenUsage(total("prompt_tokens"), total("completion_tokens"), total("total_tokens"))

    def as_envelope(self) -> dict[str, Any]:
        envelope = {
            "schema_version": "1.0", "output": _thaw(self.output),
            "metadata": {
                "mode": self.mode, "routed_intent": self.routed_intent,
                "router_source": self.router_source, "router_confidence": self.router_confidence,
                "status": self.output["status"], "provider": self.provider,
                "model": self.model, "request_id": self.request_id,
                "latency_ms": self.latency_ms, "usage": _usage_dict(self.usage),
                "finish_reason": self.finish_reason, "fallback_reason": self.fallback_reason,
                "refusal_reason": self.refusal_reason,
                "provider_attempts": self.provider_attempts, "repair_attempts": self.repair_attempts,
                "attempts": [attempt.as_dict() for attempt in self.attempts],
                "total_latency_ms": self.total_latency_ms, "total_usage": _usage_dict(self.total_usage),
                "persistence_status": self.persistence_status, "revision_rounds": self.revision_rounds,
            },
        }
        return copy.deepcopy(envelope)
"""Minimal synchronous client for the Beijing OpenAI-compatible Qwen API."""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_MODEL = "qwen-plus"
DEFAULT_TIMEOUT_SECONDS = 30.0
FALLBACK_MESSAGE = "解释服务暂时不可用，请稍后重试。"


class QwenErrorCode(str, Enum):
    """Stable, non-sensitive failure categories for an outer fallback layer."""

    CONFIG = "CONFIG"
    INVALID_REQUEST = "INVALID_REQUEST"
    TIMEOUT = "TIMEOUT"
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK = "NETWORK"
    HTTP = "HTTP"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class QwenClientError(RuntimeError):
    """A provider failure safe to expose to deterministic orchestration code."""

    def __init__(
        self,
        code: QwenErrorCode,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        fallback_message: str = FALLBACK_MESSAGE,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.fallback_message = fallback_message
        super().__init__(f"{code.value}: {fallback_message}")

    def as_metadata(self) -> dict[str, str | int | None]:
        return {
            "error_code": self.code.value,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "fallback_message": self.fallback_message,
        }


@dataclass(frozen=True)
class QwenConfig:
    api_key: str = field(repr=False)
    workspace_id: str
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.workspace_id.strip() or not self.model.strip():
            raise QwenClientError(QwenErrorCode.CONFIG)
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in self.workspace_id):
            raise QwenClientError(QwenErrorCode.CONFIG)
        if self.timeout_seconds <= 0:
            raise QwenClientError(QwenErrorCode.CONFIG)

    @property
    def endpoint(self) -> str:
        return (
            f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1/chat/completions"
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "QwenConfig":
        values = os.environ if environ is None else environ
        try:
            timeout_seconds = float(
                values.get("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            )
        except (TypeError, ValueError) as exc:
            raise QwenClientError(QwenErrorCode.CONFIG) from exc
        return cls(
            api_key=values.get("DASHSCOPE_API_KEY", ""),
            workspace_id=values.get("DASHSCOPE_WORKSPACE_ID", ""),
            model=values.get("QWEN_MODEL", DEFAULT_MODEL),
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class TransportRequest:
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class SyncTransport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse: ...


class UrllibTransport:
    """Boring standard-library transport; no provider policy lives here."""

    def send(self, request: TransportRequest) -> TransportResponse:
        http_request = Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=request.timeout_seconds) as response:
                return TransportResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as exc:
            return TransportResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("Qwen request timed out") from exc
            raise OSError("Qwen network request failed") from exc


@dataclass(frozen=True)
class QwenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class QwenResponse:
    text: str
    request_id: str | None
    response_id: str | None
    model: str
    usage: QwenUsage
    finish_reason: str | None
    latency_ms: float


class QwenClient:
    """Provider adapter only; intent, retrieval, and fallback assembly stay outside."""

    def __init__(
        self,
        config: QwenConfig,
        *,
        transport: SyncTransport | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.config = config
        self._transport = transport or UrllibTransport()
        self._clock = clock

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> QwenResponse:
        normalized_messages = _normalize_messages(messages)
        if parameters is None:
            body: dict[str, Any] = {}
        elif isinstance(parameters, Mapping):
            body = dict(parameters)
        else:
            raise QwenClientError(QwenErrorCode.INVALID_REQUEST)
        if "stream" in body and body["stream"] is not False:
            raise QwenClientError(QwenErrorCode.INVALID_REQUEST)
        # Provider configuration and caller messages are authoritative. Optional
        # generation parameters cannot silently redirect the request.
        body["model"] = self.config.model
        body["messages"] = normalized_messages
        try:
            encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise QwenClientError(QwenErrorCode.INVALID_REQUEST) from exc
        request = TransportRequest(
            url=self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            body=encoded_body,
            timeout_seconds=self.config.timeout_seconds,
        )

        started = self._clock()
        try:
            response = self._transport.send(request)
        except (TimeoutError, socket.timeout) as exc:
            raise QwenClientError(QwenErrorCode.TIMEOUT) from exc
        except OSError as exc:
            raise QwenClientError(QwenErrorCode.NETWORK) from exc
        latency_ms = max(0.0, (self._clock() - started) * 1000)
        request_id = _header(response.headers, "x-request-id") or _header(
            response.headers, "x-dashscope-request-id"
        )

        if response.status_code in {401, 403}:
            raise QwenClientError(
                QwenErrorCode.AUTH,
                status_code=response.status_code,
                request_id=request_id,
            )
        if response.status_code == 429:
            raise QwenClientError(
                QwenErrorCode.RATE_LIMIT,
                status_code=response.status_code,
                request_id=request_id,
            )
        if not 200 <= response.status_code < 300:
            raise QwenClientError(
                QwenErrorCode.HTTP,
                status_code=response.status_code,
                request_id=request_id,
            )

        payload = _decode_payload(response.body, request_id=request_id)
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise QwenClientError(
                QwenErrorCode.INVALID_RESPONSE, request_id=request_id
            ) from exc
        if not isinstance(text, str):
            raise QwenClientError(QwenErrorCode.INVALID_RESPONSE, request_id=request_id)

        response_id = _optional_string(payload.get("id"))
        request_id = (
            request_id
            or _optional_string(payload.get("request_id"))
            or response_id
        )
        model = _optional_string(payload.get("model")) or self.config.model
        finish_reason = _optional_string(choice.get("finish_reason"))
        usage = _parse_usage(payload.get("usage"), request_id=request_id)
        return QwenResponse(
            text=text,
            request_id=request_id,
            response_id=response_id,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _normalize_messages(messages: Any) -> list[dict[str, Any]]:
    if (
        isinstance(messages, (str, bytes))
        or not isinstance(messages, Sequence)
        or not messages
    ):
        raise QwenClientError(QwenErrorCode.INVALID_REQUEST)
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise QwenClientError(QwenErrorCode.INVALID_REQUEST)
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise QwenClientError(QwenErrorCode.INVALID_REQUEST)
        normalized.append(dict(message))
    return normalized


def _decode_payload(body: bytes, *, request_id: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenClientError(
            QwenErrorCode.INVALID_RESPONSE, request_id=request_id
        ) from exc
    if not isinstance(payload, dict):
        raise QwenClientError(QwenErrorCode.INVALID_RESPONSE, request_id=request_id)
    return payload


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_usage(value: Any, *, request_id: str | None) -> QwenUsage:
    if value is None:
        return QwenUsage()
    if not isinstance(value, dict):
        raise QwenClientError(QwenErrorCode.INVALID_RESPONSE, request_id=request_id)
    return QwenUsage(
        prompt_tokens=_optional_token_count(value.get("prompt_tokens"), request_id),
        completion_tokens=_optional_token_count(
            value.get("completion_tokens"), request_id
        ),
        total_tokens=_optional_token_count(value.get("total_tokens"), request_id),
    )


def _optional_token_count(value: Any, request_id: str | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QwenClientError(QwenErrorCode.INVALID_RESPONSE, request_id=request_id)
    return value

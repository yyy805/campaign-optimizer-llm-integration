from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from campaign_optimizer.llm.qwen_client import (
    FALLBACK_MESSAGE,
    QwenClient,
    QwenClientError,
    QwenConfig,
    QwenErrorCode,
    TransportRequest,
    TransportResponse,
)


@dataclass
class FakeTransport:
    response: TransportResponse | None = None
    error: Exception | None = None
    request: TransportRequest | None = None

    def send(self, request: TransportRequest) -> TransportResponse:
        self.request = request
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def config(**overrides: object) -> QwenConfig:
    values = {
        "api_key": "sk-secret-test-value",
        "workspace_id": "ws-test",
        "model": "qwen-plus",
        "timeout_seconds": 30.0,
    }
    values.update(overrides)
    return QwenConfig(**values)


def response(status: int = 200, payload: object | None = None) -> TransportResponse:
    if payload is None:
        payload = {
            "id": "chatcmpl-123",
            "request_id": "req-body",
            "model": "qwen-plus-2026-01-01",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        }
    return TransportResponse(
        status_code=status,
        headers={"x-request-id": "req-header"},
        body=json.dumps(payload).encode("utf-8"),
    )


def test_config_from_environment_uses_defaults_and_beijing_endpoint():
    loaded = QwenConfig.from_env(
        {
            "DASHSCOPE_API_KEY": "sk-secret-test-value",
            "DASHSCOPE_WORKSPACE_ID": "workspace-123",
        }
    )

    assert loaded.model == "qwen-plus"
    assert loaded.timeout_seconds == 30.0
    assert loaded.endpoint == (
        "https://workspace-123.cn-beijing.maas.aliyuncs.com/"
        "compatible-mode/v1/chat/completions"
    )


def test_fallback_message_preserves_expected_chinese_text():
    assert FALLBACK_MESSAGE == "解释服务暂时不可用，请稍后重试。"


@pytest.mark.parametrize("missing", ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"])
def test_config_rejects_missing_required_environment(missing):
    environ = {
        "DASHSCOPE_API_KEY": "sk-secret-test-value",
        "DASHSCOPE_WORKSPACE_ID": "workspace-123",
    }
    environ.pop(missing)

    with pytest.raises(QwenClientError) as caught:
        QwenConfig.from_env(environ)

    assert caught.value.code is QwenErrorCode.CONFIG
    assert caught.value.fallback_message == FALLBACK_MESSAGE


def test_config_rejects_workspace_endpoint_injection():
    with pytest.raises(QwenClientError) as caught:
        config(workspace_id="workspace.example.com/path")

    assert caught.value.code is QwenErrorCode.CONFIG


def test_config_rejects_invalid_timeout():
    with pytest.raises(QwenClientError) as caught:
        QwenConfig.from_env(
            {
                "DASHSCOPE_API_KEY": "sk-secret-test-value",
                "DASHSCOPE_WORKSPACE_ID": "workspace-123",
                "LLM_TIMEOUT_SECONDS": "zero",
            }
        )

    assert caught.value.code is QwenErrorCode.CONFIG


def test_success_returns_structured_metadata_and_sends_expected_request():
    transport = FakeTransport(response=response())
    client = QwenClient(config(), transport=transport, clock=lambda: 10.0)

    result = client.chat([{"role": "user", "content": "hello"}])

    assert result.text == "hello"
    assert result.request_id == "req-header"
    assert result.response_id == "chatcmpl-123"
    assert result.model == "qwen-plus-2026-01-01"
    assert result.finish_reason == "stop"
    assert result.usage.prompt_tokens == 12
    assert result.usage.completion_tokens == 3
    assert result.usage.total_tokens == 15
    assert result.latency_ms == 0.0
    assert transport.request is not None
    assert transport.request.url == config().endpoint
    assert json.loads(transport.request.body)["model"] == "qwen-plus"
    assert transport.request.headers["Authorization"] == "Bearer sk-secret-test-value"


def test_parameters_cannot_override_configured_model_or_messages():
    transport = FakeTransport(response=response())
    client = QwenClient(config(), transport=transport)

    client.chat(
        [{"role": "user", "content": "authoritative"}],
        parameters={"model": "other-model", "messages": [], "temperature": 0},
    )

    assert transport.request is not None
    payload = json.loads(transport.request.body)
    assert payload["model"] == "qwen-plus"
    assert payload["messages"] == [{"role": "user", "content": "authoritative"}]
    assert payload["temperature"] == 0


@pytest.mark.parametrize(
    "messages",
    [
        [],
        "not-a-message-list",
        b"not-a-message-list",
        [42],
        [{"content": "missing role"}],
        [{"role": "user"}],
        [{"role": 42, "content": "hello"}],
        [{"role": "user", "content": 42}],
    ],
)
def test_invalid_messages_are_rejected_before_transport(messages):
    transport = FakeTransport(response=response())
    client = QwenClient(config(), transport=transport)

    with pytest.raises(QwenClientError) as caught:
        client.chat(messages)

    assert caught.value.code is QwenErrorCode.INVALID_REQUEST
    assert transport.request is None


def test_unserializable_parameters_are_rejected_before_transport_without_leakage():
    secret = "sensitive-input-value"
    transport = FakeTransport(response=response())
    client = QwenClient(config(), transport=transport)

    with pytest.raises(QwenClientError) as caught:
        client.chat(
            [{"role": "user", "content": secret}],
            parameters={"metadata": object()},
        )

    assert caught.value.code is QwenErrorCode.INVALID_REQUEST
    assert transport.request is None
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in repr(caught.value.as_metadata())


def test_streaming_is_rejected_before_transport():
    transport = FakeTransport(response=response())
    client = QwenClient(config(), transport=transport)

    with pytest.raises(QwenClientError) as caught:
        client.chat(
            [{"role": "user", "content": "hello"}],
            parameters={"stream": True},
        )

    assert caught.value.code is QwenErrorCode.INVALID_REQUEST
    assert transport.request is None


def test_key_is_redacted_from_config_request_client_and_error_representations():
    secret = "sk-secret-test-value"
    transport = FakeTransport(response=response(status=401, payload={"message": secret}))
    client = QwenClient(config(api_key=secret), transport=transport)

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert secret not in repr(client.config)
    assert secret not in repr(transport.request)
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.as_metadata()["fallback_message"] == FALLBACK_MESSAGE


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_classified(status):
    client = QwenClient(config(), transport=FakeTransport(response=response(status, {})))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.AUTH
    assert caught.value.status_code == status
    assert caught.value.request_id == "req-header"


def test_rate_limit_is_classified():
    client = QwenClient(config(), transport=FakeTransport(response=response(429, {})))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.RATE_LIMIT


def test_timeout_is_classified():
    client = QwenClient(config(), transport=FakeTransport(error=TimeoutError()))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.TIMEOUT


def test_network_error_is_classified():
    client = QwenClient(config(), transport=FakeTransport(error=OSError("offline")))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.NETWORK


@pytest.mark.parametrize(
    "bad_response",
    [
        TransportResponse(200, {}, b"not-json"),
        response(200, {"choices": []}),
        response(200, {"choices": [{"message": {"content": 42}}]}),
    ],
)
def test_invalid_response_is_classified(bad_response):
    client = QwenClient(config(), transport=FakeTransport(response=bad_response))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.INVALID_RESPONSE
    assert caught.value.fallback_message == FALLBACK_MESSAGE


def test_other_http_failure_is_classified_without_body_leakage():
    secret_body = {"message": "provider internal detail"}
    client = QwenClient(config(), transport=FakeTransport(response=response(500, secret_body)))

    with pytest.raises(QwenClientError) as caught:
        client.chat([{"role": "user", "content": "hello"}])

    assert caught.value.code is QwenErrorCode.HTTP
    assert "provider internal detail" not in str(caught.value)

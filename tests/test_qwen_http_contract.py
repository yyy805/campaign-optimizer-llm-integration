from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from campaign_optimizer.llm.qwen_client import (
    QwenClient,
    QwenClientError,
    QwenErrorCode,
    UrllibTransport,
)

SYNTHETIC_KEY = "synthetic-loopback-key-never-print"


class LoopbackConfig:
    def __init__(self, endpoint: str, *, timeout_seconds: float = 1.0) -> None:
        self.api_key = SYNTHETIC_KEY
        self.workspace_id = "loopback"
        self.model = "qwen-loopback"
        self.timeout_seconds = timeout_seconds
        self.endpoint = endpoint


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.behavior = "success"
        self.captured: list[dict[str, object]] = []


class Handler(BaseHTTPRequestHandler):
    server: ContractServer

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.captured.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(body),
            }
        )
        behavior = self.server.behavior
        if behavior == "timeout":
            time.sleep(0.25)
            return
        if behavior == "disconnect":
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        status = int(behavior) if behavior.isdigit() else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-request-id", "loopback-request")
        self.end_headers()
        if behavior == "invalid_json":
            self.wfile.write(b"not-json")
        elif status == 200:
            self.wfile.write(
                json.dumps(
                    {
                        "id": "response-id",
                        "model": "qwen-provider-value",
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "pong"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 1,
                            "total_tokens": 4,
                        },
                    }
                ).encode("utf-8")
            )
        else:
            self.wfile.write(b'{"message":"provider-sensitive-body"}')

    def log_message(self, format, *args):
        return


@pytest.fixture
def loopback_server():
    server = ContractServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    endpoint = f"http://{host}:{port}/compatible-mode/v1/chat/completions"
    try:
        yield server, endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def client(endpoint: str, *, timeout_seconds: float = 1.0) -> QwenClient:
    return QwenClient(
        LoopbackConfig(endpoint, timeout_seconds=timeout_seconds),
        transport=UrllibTransport(),
    )


def test_loopback_success_uses_real_urllib_and_expected_http_contract(loopback_server):
    server, endpoint = loopback_server
    result = client(endpoint).chat(
        [{"role": "user", "content": "ping"}],
        parameters={"temperature": 0, "stream": False},
    )

    assert result.text == "pong"
    assert result.request_id == "loopback-request"
    assert result.model == "qwen-provider-value"
    captured = server.captured[0]
    assert captured["path"] == "/compatible-mode/v1/chat/completions"
    assert captured["authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert captured["content_type"] == "application/json"
    assert captured["body"] == {
        "temperature": 0,
        "stream": False,
        "model": "qwen-loopback",
        "messages": [{"role": "user", "content": "ping"}],
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [("401", QwenErrorCode.AUTH), ("403", QwenErrorCode.AUTH),
     ("429", QwenErrorCode.RATE_LIMIT), ("500", QwenErrorCode.HTTP)],
)
def test_loopback_http_errors_are_classified_without_leakage(
    loopback_server, status, code
):
    server, endpoint = loopback_server
    server.behavior = status
    with pytest.raises(QwenClientError) as caught:
        client(endpoint).chat([{"role": "user", "content": "ping"}])
    assert caught.value.code is code
    assert caught.value.status_code == int(status)
    assert caught.value.request_id == "loopback-request"
    exposed = str(caught.value) + repr(caught.value) + repr(caught.value.as_metadata())
    assert SYNTHETIC_KEY not in exposed
    assert "provider-sensitive-body" not in exposed


def test_loopback_invalid_json_preserves_request_id(loopback_server):
    server, endpoint = loopback_server
    server.behavior = "invalid_json"
    with pytest.raises(QwenClientError) as caught:
        client(endpoint).chat([{"role": "user", "content": "ping"}])
    assert caught.value.code is QwenErrorCode.INVALID_RESPONSE
    assert caught.value.request_id == "loopback-request"
    assert SYNTHETIC_KEY not in repr(caught.value)


def test_loopback_timeout_is_classified(loopback_server):
    server, endpoint = loopback_server
    server.behavior = "timeout"
    with pytest.raises(QwenClientError) as caught:
        client(endpoint, timeout_seconds=0.05).chat(
            [{"role": "user", "content": "ping"}]
        )
    assert caught.value.code is QwenErrorCode.TIMEOUT
    assert SYNTHETIC_KEY not in repr(caught.value)


def test_loopback_disconnect_is_network_error(loopback_server):
    server, endpoint = loopback_server
    server.behavior = "disconnect"
    with pytest.raises(QwenClientError) as caught:
        client(endpoint).chat([{"role": "user", "content": "ping"}])
    assert caught.value.code is QwenErrorCode.NETWORK
    assert SYNTHETIC_KEY not in repr(caught.value)

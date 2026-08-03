"""Opt-in smoke test for the real Beijing-region Qwen API."""

from __future__ import annotations

import os

import pytest

from campaign_optimizer.llm import QwenClient, QwenConfig


@pytest.mark.skipif(
    os.getenv("LLM_REAL_API_TESTS") != "1",
    reason="set LLM_REAL_API_TESTS=1 to enable the paid real API smoke test",
)
def test_real_qwen_api_smoke():
    client = QwenClient(QwenConfig.from_env())

    result = client.chat(
        [{"role": "user", "content": "Reply with exactly: pong"}],
        parameters={"max_tokens": 16, "temperature": 0},
    )

    assert result.text.strip()
    assert result.request_id
    assert result.model
    assert result.usage.total_tokens is None or result.usage.total_tokens > 0

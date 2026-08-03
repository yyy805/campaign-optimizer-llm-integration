"""Explicit, paid smoke test for the Beijing workspace Qwen endpoint."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from campaign_optimizer.llm.qwen_client import (
    QwenClient,
    QwenClientError,
    QwenConfig,
    QwenErrorCode,
)


def execute_smoke(
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[QwenConfig], Any] = QwenClient,
) -> dict[str, Any]:
    """Make one real call when invoked; never return model response content."""
    config = QwenConfig.from_env(environ)
    result = client_factory(config).chat(
        [{"role": "user", "content": "Reply with exactly: pong"}],
        parameters={"max_tokens": 16, "temperature": 0, "stream": False},
    )
    return {
        "ok": True,
        "model": result.model,
        "request_id": result.request_id,
        "latency_ms": result.latency_ms,
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
        },
    }


def main(
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[QwenConfig], Any] = QwenClient,
) -> int:
    try:
        summary = execute_smoke(environ=environ, client_factory=client_factory)
    except QwenClientError as exc:
        if exc.code is QwenErrorCode.CONFIG:
            print(
                "Configuration missing or invalid. Set DASHSCOPE_API_KEY and "
                "DASHSCOPE_WORKSPACE_ID as environment variables; QWEN_MODEL "
                "is optional.",
                file=sys.stderr,
            )
            return 2
        summary = {
            "ok": False,
            "model": None,
            "request_id": None,
            "latency_ms": None,
            "usage": None,
        }
        print("Qwen smoke request failed safely.", file=sys.stderr)
        print(json.dumps(summary, sort_keys=True))
        return 1
    except Exception:
        summary = {
            "ok": False,
            "model": None,
            "request_id": None,
            "latency_ms": None,
            "usage": None,
        }
        print("Qwen smoke request failed safely.", file=sys.stderr)
        print(json.dumps(summary, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

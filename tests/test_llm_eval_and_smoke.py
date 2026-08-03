from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from campaign_optimizer.llm.eval_runner import run_offline_eval
from campaign_optimizer.llm.qwen_client import QwenUsage
from scripts import run_llm_eval, smoke_qwen


def test_fixed_offline_eval_suite_passes_without_sensitive_content():
    summary = run_offline_eval()

    assert summary["offline"] is True
    assert summary["total"] == 17
    assert summary["passed"] == 17
    assert summary["failed"] == 0
    serialized = json.dumps(summary, ensure_ascii=False)
    for forbidden in ['"question":', '"prompt":', '"response":', '"answer":', "Sponsored Products"]:
        assert forbidden not in serialized


def test_eval_suite_covers_required_l5_cases():
    case_ids = {result["case_id"] for result in run_offline_eval()["results"]}
    assert {
        "initial_success",
        "chat_success",
        "forbidden_internal",
        "prompt_injection",
        "invalid_json_repaired",
        "invalid_json_twice",
        "tamper_numeric",
        "tamper_plan_id",
        "tamper_fact_id",
        "tamper_rule_id",
        "tamper_verdict",
        "tamper_limitations",
        "provider_timeout",
        "provider_auth",
        "provider_rate_limit",
        "provider_network",
        "no_key",
    } == case_ids


class _SafeFakeClient:
    def __init__(self, config) -> None:
        self.config = config

    def chat(self, messages, *, parameters=None):
        return SimpleNamespace(
            text="sensitive model response must not be printed",
            model=self.config.model,
            request_id="request_safe",
            latency_ms=12.5,
            usage=QwenUsage(3, 1, 4),
        )


def test_smoke_summary_contains_only_safe_operational_metadata():
    environ = {
        "DASHSCOPE_API_KEY": "synthetic-secret-never-print",
        "DASHSCOPE_WORKSPACE_ID": "workspace-test",
        "QWEN_MODEL": "qwen-plus",
    }
    summary = smoke_qwen.execute_smoke(
        environ=environ, client_factory=_SafeFakeClient
    )

    assert set(summary) == {"ok", "model", "request_id", "latency_ms", "usage"}
    serialized = json.dumps(summary)
    assert "synthetic-secret-never-print" not in serialized
    assert "workspace-test" not in serialized
    assert "sensitive model response" not in serialized


def test_smoke_missing_env_is_clear_safe_and_makes_no_client(capsys):
    factory_called = False

    def forbidden_factory(config):
        nonlocal factory_called
        factory_called = True
        raise AssertionError("missing config must fail before client construction")

    exit_code = smoke_qwen.main(environ={}, client_factory=forbidden_factory)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert factory_called is False
    assert captured.out == ""
    assert "DASHSCOPE_API_KEY" in captured.err
    assert "DASHSCOPE_WORKSPACE_ID" in captured.err
    assert "QWEN_MODEL" in captured.err

def test_eval_cli_missing_manifest_is_fixed_safe_failure(tmp_path, capsys):
    missing = tmp_path / "private-user" / "missing-manifest.json"
    exit_code = run_llm_eval.main(["--manifest", str(missing)])
    captured = capsys.readouterr()

    assert exit_code != 0
    summary = json.loads(captured.out)
    assert summary == {
        "schema_version": "1.0",
        "suite_id": "unavailable",
        "offline": True,
        "total": 0,
        "passed": 0,
        "failed": 1,
        "results": [],
    }
    combined = captured.out + captured.err
    assert "Traceback" not in combined
    assert str(missing) not in combined
    assert "private-user" not in combined


def test_smoke_unexpected_error_is_fixed_safe_failure(capsys):
    def failing_factory(config):
        raise RuntimeError("sensitive-value")

    exit_code = smoke_qwen.main(
        environ={
            "DASHSCOPE_API_KEY": "synthetic-secret-never-print",
            "DASHSCOPE_WORKSPACE_ID": "workspace-test",
        },
        client_factory=failing_factory,
    )
    captured = capsys.readouterr()

    assert exit_code != 0
    assert set(json.loads(captured.out)) == {
        "ok", "model", "request_id", "latency_ms", "usage"
    }
    combined = captured.out + captured.err
    assert "Traceback" not in combined
    assert "sensitive-value" not in combined
    assert "synthetic-secret-never-print" not in combined
    assert "workspace-test" not in combined

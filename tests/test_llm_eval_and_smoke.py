from __future__ import annotations

import json

import pytest
from pathlib import Path
from types import SimpleNamespace

from campaign_optimizer.llm.eval_runner import run_offline_eval
from campaign_optimizer.llm import eval_runner
from campaign_optimizer.llm.qwen_client import QwenUsage
from scripts import run_llm_eval, smoke_qwen


PLAN_FIXTURES = Path(__file__).parent / "fixtures" / "plan_a"


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


@pytest.mark.parametrize(
    "scenario,assert_mutation",
    [
        (
            "tamper_fact_id",
            lambda output: any(
                claim.get("claim_type") == "FACT_VALUE"
                and claim.get("source_id") == "decision_fact_intruder"
                for claim in output["claims"]
            )
            and "decision_fact_intruder" in output["facts_used"],
        ),
        (
            "tamper_rule_id",
            lambda output: any(
                claim.get("claim_type") == "RULE_FIELD"
                and claim.get("source_id") == "R1"
                for claim in output["claims"]
            )
            and output["rule_ids_used"] == ["R1"],
        ),
        (
            "tamper_limitations",
            lambda output: any(
                claim.get("claim_type") == "REVIEW_FIELD"
                and claim.get("field") == "limitations"
                and claim.get("value") == "limitation removed"
                for claim in output["claims"]
            ),
        ),
    ],
)
def test_named_tamper_scenarios_mutate_their_semantic_target(
    scenario, assert_mutation
):
    output = json.loads(
        (PLAN_FIXTURES / "llm_workflow_output.demo.json").read_text(
            encoding="utf-8"
        )
    )

    eval_runner._tamper(output, scenario)

    assert assert_mutation(output)


@pytest.mark.parametrize(
    "case_id", ["tamper_fact_id", "tamper_rule_id", "tamper_limitations"]
)
def test_named_tamper_scenarios_fail_closed_after_retry(case_id):
    result = {
        item["case_id"]: item for item in run_offline_eval()["results"]
    }[case_id]

    assert result["passed"] is True
    assert result["actual_status"] == "FALLBACK"
    assert result["provider_calls"] == 2
    assert result["repair_used"] is True


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

def test_smoke_rejects_empty_success_metadata_without_leakage(capsys):
    class InvalidSuccessClient:
        def __init__(self, config):
            self.config = config

        def chat(self, messages, *, parameters=None):
            return SimpleNamespace(
                text="",
                model="qwen-plus",
                request_id="request-safe",
                latency_ms=1.0,
                usage=QwenUsage(1, 1, 2),
            )

    exit_code = smoke_qwen.main(
        environ={
            "DASHSCOPE_API_KEY": "synthetic-secret-never-print",
            "DASHSCOPE_WORKSPACE_ID": "workspace-test",
        },
        client_factory=InvalidSuccessClient,
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out)["ok"] is False
    exposed = captured.out + captured.err
    assert "synthetic-secret-never-print" not in exposed
    assert "workspace-test" not in exposed

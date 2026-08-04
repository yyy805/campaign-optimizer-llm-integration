"""v7 runner: exact safe schema diagnostics plus Executor-only token limit."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from campaign_optimizer.contracts.validation import ContractValidationError

from .agent_workflow_v7 import RoleConfigurationV7, load_role_configuration
from .diagnostic_output_guard import SafeOutputValidationFailure
from .schema_diagnostic_guard_v7 import DiagnosticOutputGuardV7, SchemaDiagnosticFailure
from .three_role_runner import (
    RoleCallAdapter,
    RoleCallFailure,
    ThreeRoleRunner,
    _append,
    _error_audit,
    _executor_payload,
    _response_audit,
)
from .qwen_client import QwenClientError


class RoleCallAdapterV7(RoleCallAdapter):
    """Adds a generation ceiling to Executor without changing other roles."""

    def __init__(self, configuration: RoleConfigurationV7, *, client_factory=None):
        self._v7_configuration = configuration
        super().__init__(configuration.roles, client_factory=client_factory)

    def call_json(self, *, role: str, payload: Mapping[str, Any]):
        model = self._configuration.model_aliases[role]
        parameters: dict[str, Any] = {
            "temperature": 0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        if role == "executor":
            parameters["max_tokens"] = self._v7_configuration.executor_max_output_tokens
        try:
            response = self._client_factory(role, model).chat(
                (
                    {"role": "system", "content": self._prompts[role]},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
                ),
                parameters=parameters,
            )
        except QwenClientError as exc:
            raise RoleCallFailure(_error_audit(role, model, exc), repairable=False) from exc
        except (OSError, TimeoutError):
            from .three_role_runner import RoleCallAudit
            raise RoleCallFailure(RoleCallAudit(0, role, model, "PROVIDER_ERROR", "NETWORK"), repairable=False) from None
        audit = _response_audit(role, model, response)
        try:
            parsed = json.loads(response.text)
        except (TypeError, json.JSONDecodeError):
            raise RoleCallFailure(replace(audit, outcome="INVALID_JSON", error_code="INVALID_JSON"), repairable=True) from None
        if not isinstance(parsed, Mapping):
            raise RoleCallFailure(replace(audit, outcome="INVALID_JSON", error_code="INVALID_JSON"), repairable=True)
        return dict(parsed), audit


class ThreeRoleRunnerV7(ThreeRoleRunner):
    def __init__(self, *, configuration: RoleConfigurationV7 | None = None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        self._v7_configuration = config
        super().__init__(
            configuration=config.roles,
            role_calls=role_calls or RoleCallAdapterV7(config),
            output_guard=output_guard or DiagnosticOutputGuardV7(),
        )

    def _executor_candidate(self, request, plan, review, context, intent, n, actions, calls):
        payload = _executor_payload(request, context, intent, n, actions)
        candidate, failure = self._attempt_executor(payload, request, plan, review, context, n, calls, repair=False)
        if candidate is not None or failure is None or not failure.startswith("EXECUTOR_"):
            return candidate, failure
        category, validator, path = _split_safe_code(failure)
        repair_payload = copy.deepcopy(payload)
        repair_payload["server_format_repair"] = {
            "validation_category": category,
            "validation_path": path,
            "validator": validator,
            "instruction": "Return a complete corrected JSON object under the pinned contract.",
        }
        return self._attempt_executor(repair_payload, request, plan, review, context, n, calls, repair=True)

    def _attempt_executor(self, payload, request, plan, review, context, n, calls, *, repair: bool):
        prefix = "EXECUTOR_REPAIR" if repair else "EXECUTOR"
        try:
            candidate, audit = self._role_calls.call_json(role="executor", payload=payload)
            _append(calls, audit)
            return self._validated(candidate, request, plan, review, context, n), None
        except RoleCallFailure as exc:
            _append(calls, exc.audit)
            if not exc.repairable:
                return None, exc.audit.error_code or f"{prefix}_PROVIDER_ERROR"
            code = f"{prefix}_JSON.parse:output"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code
        except SchemaDiagnosticFailure as exc:
            code = f"{prefix}_SCHEMA.{exc.validator}:{exc.path}"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code
        except SafeOutputValidationFailure as exc:
            code = f"{prefix}_{exc.category}.guard:{exc.path}"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code
        except (ContractValidationError, ValueError, KeyError, TypeError):
            code = f"{prefix}_EXCHANGE.guard:output.exchange"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code


def _split_safe_code(code: str) -> tuple[str, str, str]:
    body = code.removeprefix("EXECUTOR_")
    category_validator, _, path = body.partition(":")
    category, _, validator = category_validator.partition(".")
    return category, validator or "guard", path or "output"

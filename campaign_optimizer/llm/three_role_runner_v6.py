"""v6 Executor alignment layered over the reviewed v5 trust boundary."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from campaign_optimizer.contracts.validation import ContractValidationError

from .agent_workflow_v6 import load_role_configuration
from .diagnostic_output_guard import DiagnosticOutputGuard, SafeOutputValidationFailure
from .three_role_runner import (
    RoleCallAdapter,
    RoleCallFailure,
    ThreeRoleRunner,
    _append,
    _executor_payload,
)


class ThreeRoleRunnerV6(ThreeRoleRunner):
    """Keeps v5 caps/fallbacks while adding safe Executor diagnostics."""

    def __init__(self, *, configuration=None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        super().__init__(
            configuration=config,
            role_calls=role_calls or RoleCallAdapter(config),
            output_guard=output_guard or DiagnosticOutputGuard(),
        )

    def _executor_candidate(
        self,
        request: Mapping[str, Any],
        plan: Mapping[str, Any],
        review: Mapping[str, Any],
        context: Mapping[str, Any],
        intent: str,
        n: int,
        actions: Sequence[Mapping[str, Any]],
        calls: list,
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        payload = _executor_payload(request, context, intent, n, actions)
        candidate, failure = self._attempt_executor(
            payload, request, plan, review, context, n, calls, repair=False
        )
        if candidate is not None or failure is None or not failure.startswith("EXECUTOR_"):
            return candidate, failure
        category, path = _split_safe_code(failure)
        repair_payload = copy.deepcopy(payload)
        repair_payload["server_format_repair"] = {
            "validation_category": category,
            "validation_path": path,
            "instruction": "Return a complete corrected JSON object under the pinned contract.",
        }
        return self._attempt_executor(
            repair_payload, request, plan, review, context, n, calls, repair=True
        )

    def _attempt_executor(
        self, payload, request, plan, review, context, n, calls, *, repair: bool
    ):
        prefix = "EXECUTOR_REPAIR" if repair else "EXECUTOR"
        try:
            candidate, audit = self._role_calls.call_json(
                role="executor", payload=payload
            )
            _append(calls, audit)
            return self._validated(candidate, request, plan, review, context, n), None
        except RoleCallFailure as exc:
            _append(calls, exc.audit)
            if not exc.repairable:
                return None, exc.audit.error_code or f"{prefix}_PROVIDER_ERROR"
            code = f"{prefix}_JSON:output"
            calls[-1] = replace(
                calls[-1], outcome="CONTENT_INVALID", error_code=code
            )
            return None, code
        except SafeOutputValidationFailure as exc:
            code = f"{prefix}_{exc.category}:{exc.path}"
            calls[-1] = replace(
                calls[-1], outcome="CONTENT_INVALID", error_code=code
            )
            return None, code
        except (ContractValidationError, ValueError, KeyError, TypeError):
            code = f"{prefix}_EXCHANGE:output.exchange"
            calls[-1] = replace(
                calls[-1], outcome="CONTENT_INVALID", error_code=code
            )
            return None, code


def _split_safe_code(code: str) -> tuple[str, str]:
    category_path = code.removeprefix("EXECUTOR_")
    category, _, path = category_path.partition(":")
    return category, path or "output"

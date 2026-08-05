"""v11 runner: one bounded Reviewer retry without reusing invalid output."""
from __future__ import annotations

import copy
from dataclasses import replace

from jsonschema.exceptions import ValidationError

from campaign_optimizer.contracts.validation import ContractValidationError

from .agent_workflow_v5 import ReviewerPacket, WorkflowAction, next_action
from .agent_workflow_v9 import RoleConfigurationV9
from .agent_workflow_v11 import load_role_configuration, max_provider_calls_v11
from .reviewer_diagnostic_v10 import ReviewerDecisionFailure, validate_reviewer_schema
from .schema_diagnostic_guard_v8 import DiagnosticOutputGuardV8
from .three_role_runner import RoleCallAudit, RoleCallFailure, ThreeRoleResult, ThreeRoleRunner, _append, _candidate_id
from .three_role_runner_v7 import RoleCallAdapterV7
from .three_role_runner_v8 import ThreeRoleRunnerV8


class ThreeRoleRunnerV11(ThreeRoleRunnerV8):
    def __init__(self, *, configuration: RoleConfigurationV9 | None = None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        self._v7_configuration = config
        ThreeRoleRunner.__init__(self, configuration=config.roles, role_calls=role_calls or RoleCallAdapterV7(config), output_guard=output_guard or DiagnosticOutputGuardV8())

    def _execute(self, request, plan, review, context, intent, rounds, triage_used, dry_run, calls=None):
        reserved = max_provider_calls_v11(max_revision_rounds=rounds, triage_used=triage_used)
        if dry_run:
            return ThreeRoleResult("DRY_RUN", None, intent, 0, reserved, _reserved_calls_v11(self._configuration, rounds, triage_used))
        calls = [] if calls is None else calls
        actions = []
        for revision_round in range(rounds + 1):
            candidate, failure = self._executor_candidate(request, plan, review, context, intent, revision_round, actions, calls)
            if failure:
                return self._fallback(intent, revision_round, reserved, calls, failure)
            assert candidate is not None
            try:
                packet = ReviewerPacket.from_validated_exchange(request=request, plan=plan, review=review, context=context, candidate_output=candidate, resolved_intent=intent, candidate_id=_candidate_id(str(request["request_id"]), revision_round), retry_count=revision_round, config=self._configuration)
            except (ContractValidationError, ValidationError, ValueError, KeyError, TypeError):
                return self._fallback(intent, revision_round, reserved, calls, "REVIEWER_PACKET_INVALID")

            action, failure, repairable = self._attempt_reviewer(packet, revision_round, rounds, calls, retry=False)
            if failure and repairable:
                action, failure, _ = self._attempt_reviewer(packet, revision_round, rounds, calls, retry=True, safe_reason=_safe_retry_reason(failure))
            if failure:
                return self._fallback(intent, revision_round, reserved, calls, failure)
            if action is WorkflowAction.FINAL:
                return ThreeRoleResult("OK", copy.deepcopy(candidate), intent, revision_round, reserved, tuple(calls))
            if action is WorkflowAction.FALLBACK:
                return self._fallback(intent, revision_round, reserved, calls, "REVIEWER_REJECT_OR_CAP")
            assert action is WorkflowAction.REVISE
            # The validated decision is returned via the private scratch field.
            actions = copy.deepcopy(self._last_revision_actions)
        raise AssertionError("loop exhaustion")

    def _attempt_reviewer(self, packet, revision_round, rounds, calls, *, retry, safe_reason=None):
        payload = dict(packet.as_model_input())
        if retry:
            payload["server_format_retry"] = {"attempt": 1, "reason": safe_reason, "instruction": "Audit again and return exactly the seven-key JSON contract."}
        prefix = "REVIEWER_RETRY" if retry else "REVIEWER"
        try:
            decision, audit = self._role_calls.call_json(role="reviewer", payload=payload)
            _append(calls, audit)
            validate_reviewer_schema(decision)
            action = next_action(decision, packet=packet, revision_rounds=revision_round, max_revision_rounds=rounds)
            self._last_revision_actions = copy.deepcopy(decision["revision_actions"])
            return action, None, False
        except RoleCallFailure as exc:
            _append(calls, exc.audit)
            if exc.repairable:
                code = f"{prefix}_JSON.parse:reviewer"
                calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
                return None, code, not retry
            return None, exc.audit.error_code or f"{prefix}_PROVIDER_ERROR", False
        except ReviewerDecisionFailure as exc:
            code = f"{prefix}_{exc.category}.{exc.validator}:{exc.path}"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code, not retry
        except ValidationError:
            code = f"{prefix}_SCHEMA.schema:reviewer"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code, not retry
        except (ContractValidationError, ValueError, KeyError, TypeError):
            code = f"{prefix}_BINDING.guard:reviewer.decision"
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
            return None, code, not retry


def _safe_retry_reason(code: str) -> str:
    if "JSON" in code:
        return "INVALID_JSON"
    if "SCHEMA" in code:
        return "SCHEMA_MISMATCH"
    return "BINDING_MISMATCH"


def _reserved_calls_v11(configuration, rounds: int, triage: bool):
    calls = []
    if triage:
        calls.append(RoleCallAudit(0, "triage", configuration.model_aliases["triage"], "RESERVED"))
    for _ in range(rounds + 1):
        calls.extend((
            RoleCallAudit(0, "executor", configuration.model_aliases["executor"], "RESERVED"),
            RoleCallAudit(0, "reviewer", configuration.model_aliases["reviewer"], "RESERVED"),
            RoleCallAudit(0, "reviewer", configuration.model_aliases["reviewer"], "RESERVED"),
        ))
    # One Executor format repair may occur for each attempt, but the first
    # attempt is already represented above.
    calls.extend(RoleCallAudit(0, "executor", configuration.model_aliases["executor"], "RESERVED") for _ in range(rounds + 1))
    return tuple(calls)

"""v10 runner with strict, fail-closed Reviewer schema handling."""
from __future__ import annotations

import copy
from dataclasses import replace

from jsonschema.exceptions import ValidationError

from campaign_optimizer.contracts.validation import ContractValidationError

from .agent_workflow_v10 import load_role_configuration
from .agent_workflow_v5 import ReviewerPacket, WorkflowAction, next_action
from .agent_workflow_v9 import RoleConfigurationV9
from .reviewer_diagnostic_v10 import ReviewerDecisionFailure, validate_reviewer_schema
from .schema_diagnostic_guard_v8 import DiagnosticOutputGuardV8
from .three_role_runner import RoleCallFailure, ThreeRoleResult, ThreeRoleRunner, _append, _candidate_id
from .three_role_runner_v7 import RoleCallAdapterV7
from .three_role_runner_v8 import ThreeRoleRunnerV8


class ThreeRoleRunnerV10(ThreeRoleRunnerV8):
    def __init__(self, *, configuration: RoleConfigurationV9 | None = None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        self._v7_configuration = config
        ThreeRoleRunner.__init__(
            self,
            configuration=config.roles,
            role_calls=role_calls or RoleCallAdapterV7(config),
            output_guard=output_guard or DiagnosticOutputGuardV8(),
        )

    def _execute(self, request, plan, review, context, intent, rounds, triage_used, dry_run, calls=None):
        from .agent_workflow_v5 import max_provider_calls_with_repairs
        from .three_role_runner import _reserved_calls

        reserved = max_provider_calls_with_repairs(max_revision_rounds=rounds) + int(triage_used)
        if dry_run:
            return ThreeRoleResult("DRY_RUN", None, intent, 0, reserved, _reserved_calls(self._configuration, rounds, triage_used))
        calls = [] if calls is None else calls
        actions = []
        for revision_round in range(rounds + 1):
            candidate, failure = self._executor_candidate(request, plan, review, context, intent, revision_round, actions, calls)
            if failure:
                return self._fallback(intent, revision_round, reserved, calls, failure)
            assert candidate is not None
            try:
                packet = ReviewerPacket.from_validated_exchange(
                    request=request,
                    plan=plan,
                    review=review,
                    context=context,
                    candidate_output=candidate,
                    resolved_intent=intent,
                    candidate_id=_candidate_id(str(request["request_id"]), revision_round),
                    retry_count=revision_round,
                    config=self._configuration,
                )
            except (ContractValidationError, ValidationError, ValueError, KeyError, TypeError):
                return self._fallback(intent, revision_round, reserved, calls, "REVIEWER_PACKET_INVALID")
            try:
                decision, audit = self._role_calls.call_json(role="reviewer", payload=packet.as_model_input())
                _append(calls, audit)
                validate_reviewer_schema(decision)
                action = next_action(
                    decision,
                    packet=packet,
                    revision_rounds=revision_round,
                    max_revision_rounds=rounds,
                )
            except RoleCallFailure as exc:
                _append(calls, exc.audit)
                if exc.repairable:
                    calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code="REVIEWER_JSON.parse:reviewer")
                    return self._fallback(intent, revision_round, reserved, calls, "REVIEWER_JSON.parse:reviewer")
                return self._fallback(intent, revision_round, reserved, calls, exc.audit.error_code or "REVIEWER_PROVIDER_ERROR")
            except ReviewerDecisionFailure as exc:
                code = f"REVIEWER_{exc.category}.{exc.validator}:{exc.path}"
                calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
                return self._fallback(intent, revision_round, reserved, calls, code)
            except ValidationError:
                code = "REVIEWER_SCHEMA.schema:reviewer"
                calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
                return self._fallback(intent, revision_round, reserved, calls, code)
            except (ContractValidationError, ValueError, KeyError, TypeError):
                code = "REVIEWER_BINDING.guard:reviewer.decision"
                calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code=code)
                return self._fallback(intent, revision_round, reserved, calls, code)
            if action is WorkflowAction.FINAL:
                return ThreeRoleResult("OK", copy.deepcopy(candidate), intent, revision_round, reserved, tuple(calls))
            if action is WorkflowAction.FALLBACK:
                return self._fallback(intent, revision_round, reserved, calls, "REVIEWER_REJECT_OR_CAP")
            actions = copy.deepcopy(decision["revision_actions"])
        raise AssertionError("loop exhaustion")

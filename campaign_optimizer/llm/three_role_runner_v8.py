"""v8 runner with schema-derived claim_type enum repair guidance."""
from __future__ import annotations

import copy

from .agent_workflow_v7 import RoleConfigurationV7
from .agent_workflow_v8 import load_role_configuration
from .schema_diagnostic_guard_v8 import CLAIM_TYPE_ALLOWED_VALUES, DiagnosticOutputGuardV8
from .three_role_runner import ThreeRoleRunner, _executor_payload
from .three_role_runner_v7 import RoleCallAdapterV7, ThreeRoleRunnerV7, _split_safe_code


class ThreeRoleRunnerV8(ThreeRoleRunnerV7):
    def __init__(self, *, configuration: RoleConfigurationV7 | None = None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        self._v7_configuration = config
        ThreeRoleRunner.__init__(
            self,
            configuration=config.roles,
            role_calls=role_calls or RoleCallAdapterV7(config),
            output_guard=output_guard or DiagnosticOutputGuardV8(),
        )

    def _executor_candidate(self, request, plan, review, context, intent, n, actions, calls):
        payload = _executor_payload(request, context, intent, n, actions)
        candidate, failure = self._attempt_executor(payload, request, plan, review, context, n, calls, repair=False)
        if candidate is not None or failure is None or not failure.startswith("EXECUTOR_"):
            return candidate, failure
        category, validator, path = _split_safe_code(failure)
        repair_payload = copy.deepcopy(payload)
        repair_instruction = {
            "validation_category": category,
            "validation_path": path,
            "validator": validator,
            "instruction": "Return a complete corrected JSON object under the pinned contract.",
        }
        if category == "SCHEMA" and validator == "enum" and path.endswith(".claim_type"):
            repair_instruction["allowed_values"] = list(CLAIM_TYPE_ALLOWED_VALUES)
        repair_payload["server_format_repair"] = repair_instruction
        return self._attempt_executor(repair_payload, request, plan, review, context, n, calls, repair=True)

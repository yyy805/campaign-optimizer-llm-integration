"""v9 runner: temporary model mapping, unchanged v8 workflow behavior."""
from __future__ import annotations

from .agent_workflow_v9 import RoleConfigurationV9, load_role_configuration
from .schema_diagnostic_guard_v8 import DiagnosticOutputGuardV8
from .three_role_runner import ThreeRoleRunner
from .three_role_runner_v7 import RoleCallAdapterV7
from .three_role_runner_v8 import ThreeRoleRunnerV8


class ThreeRoleRunnerV9(ThreeRoleRunnerV8):
    def __init__(self, *, configuration: RoleConfigurationV9 | None = None, role_calls=None, output_guard=None):
        config = configuration or load_role_configuration()
        self._v7_configuration = config
        ThreeRoleRunner.__init__(
            self,
            configuration=config.roles,
            role_calls=role_calls or RoleCallAdapterV7(config),
            output_guard=output_guard or DiagnosticOutputGuardV8(),
        )

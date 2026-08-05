"""v13 Reviewer protocol: forced function call with thinking disabled."""
from __future__ import annotations

import copy, json
from dataclasses import replace

from .qwen_client import QwenClientError, QwenConfig, QwenErrorCode
from .qwen_function_client_v12 import MAX_TOOL_ARGUMENT_BYTES
from .qwen_function_client_v13 import QwenFunctionClientV13
from .three_role_runner import RoleCallAudit, RoleCallFailure, _error_audit
from .three_role_runner_v12 import ReviewerChannelFailure, RoleCallAdapterV12, ThreeRoleRunnerV12

class RoleCallAdapterV13(RoleCallAdapterV12):
    @staticmethod
    def _default_reviewer_factory(model):
        return QwenFunctionClientV13(replace(QwenConfig.from_env(), model=model))

    def _call_reviewer(self, payload):
        role="reviewer"; model=self._configuration.model_aliases[role]
        parameters={
            "temperature":0,
            "stream":False,
            "enable_thinking":False,
            "parallel_tool_calls":False,
            "tools":[{"type":"function","function":{"name":self._v12.tool_name,"description":"Submit one decision. No action is executed.","parameters":copy.deepcopy(self._v12.tool_schema)}}],
            "tool_choice":{"type":"function","function":{"name":self._v12.tool_name}},
        }
        messages=({"role":"system","content":self._prompts[role]},{"role":"user","content":json.dumps(payload,ensure_ascii=False,sort_keys=True)})
        try: response=self._reviewer_client_factory(model).chat(messages,parameters=parameters)
        except QwenClientError as exc:
            audit=_error_audit(role,model,exc)
            if exc.code is QwenErrorCode.INVALID_RESPONSE:audit=replace(audit,error_code="REVIEWER_PROTOCOL.invalid_response")
            raise RoleCallFailure(audit,repairable=False) from exc
        except (OSError,TimeoutError):raise RoleCallFailure(RoleCallAudit(0,role,model,"PROVIDER_ERROR","NETWORK"),repairable=False) from None
        audit=RoleCallAudit(0,role,model,"OK",request_id=response.request_id,latency_ms=response.latency_ms,total_tokens=response.usage.total_tokens)
        if response.content not in (None,""):raise self._structure(audit,"normal_text","reviewer.content")
        if len(response.tool_calls)!=1:raise self._structure(audit,"tool_count","reviewer.tool_calls")
        call=response.tool_calls[0]
        if call.name!=self._v12.tool_name:raise self._structure(audit,"tool_name","reviewer.tool_calls[0].function.name")
        if len(call.arguments.encode())>MAX_TOOL_ARGUMENT_BYTES:raise ReviewerChannelFailure(replace(audit,outcome="CONTENT_INVALID",error_code="REVIEWER_PROTOCOL.arguments_too_large"),"REVIEWER_PROTOCOL.arguments_too_large",repairable=False)
        try: decision=json.loads(call.arguments)
        except json.JSONDecodeError:raise self._structure(audit,"arguments_json","reviewer.tool_calls[0].function.arguments") from None
        from collections.abc import Mapping
        if not isinstance(decision,Mapping):raise self._structure(audit,"arguments_object","reviewer.tool_calls[0].function.arguments")
        from .reviewer_diagnostic_v10 import ReviewerDecisionFailure,validate_reviewer_schema
        try:validate_reviewer_schema(decision)
        except ReviewerDecisionFailure as exc:raise self._structure(audit,f"schema_{exc.validator}",exc.path) from None
        return dict(decision),audit

class ThreeRoleRunnerV13(ThreeRoleRunnerV12):
    def __init__(self,*,configuration=None,role_calls=None,output_guard=None):
        from .agent_workflow_v12 import load_role_configuration
        config=configuration or load_role_configuration()
        super().__init__(configuration=config,role_calls=role_calls or RoleCallAdapterV13(config),output_guard=output_guard)

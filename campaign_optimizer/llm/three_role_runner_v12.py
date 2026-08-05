"""v12 Function Calling experiment with strict channel and one budget ledger."""
from __future__ import annotations
import copy,json
from dataclasses import replace
from typing import Any,Mapping
from jsonschema.exceptions import ValidationError
from campaign_optimizer.contracts.validation import ContractValidationError
from .agent_workflow_v5 import ReviewerPacket,WorkflowAction,next_action
from .agent_workflow_v12 import BudgetExceeded,BudgetLedgerV12,RoleConfigurationV12,load_role_configuration,max_provider_calls_v12
from .qwen_client import QwenClientError,QwenConfig,QwenErrorCode
from .qwen_function_client_v12 import MAX_TOOL_ARGUMENT_BYTES,QwenFunctionClientV12
from .reviewer_diagnostic_v10 import ReviewerDecisionFailure,validate_reviewer_schema
from .schema_diagnostic_guard_v8 import DiagnosticOutputGuardV8
from .three_role_runner import RoleCallAudit,RoleCallFailure,ThreeRoleResult,ThreeRoleRunner,_append,_candidate_id,_error_audit
from .three_role_runner_v7 import RoleCallAdapterV7
from .three_role_runner_v8 import ThreeRoleRunnerV8

class ReviewerChannelFailure(RuntimeError):
    def __init__(self,audit,code,*,repairable):self.audit,self.code,self.repairable=audit,code,repairable;super().__init__("reviewer channel rejected")

class RoleCallAdapterV12(RoleCallAdapterV7):
    def __init__(self,configuration:RoleConfigurationV12,*,client_factory=None,reviewer_client_factory=None,ledger=None):
        self._v12=configuration;self.ledger=ledger or BudgetLedgerV12();self._reviewer_client_factory=reviewer_client_factory or self._default_reviewer_factory;super().__init__(configuration,client_factory=client_factory)
    @staticmethod
    def _default_reviewer_factory(model):return QwenFunctionClientV12(replace(QwenConfig.from_env(),model=model))
    def begin_candidate(self,n):self.ledger.begin_candidate(n)
    def set_total_limit(self,n):self.ledger.set_limit(n)
    def call_json(self,*,role,payload):
        try:self.ledger.consume(role)
        except BudgetExceeded:raise RoleCallFailure(RoleCallAudit(0,role,self._configuration.model_aliases[role],"BUDGET_DENIED","BUDGET_EXCEEDED"),repairable=False) from None
        return super().call_json(role=role,payload=payload) if role!="reviewer" else self._call_reviewer(payload)
    def _call_reviewer(self,payload):
        role="reviewer";model=self._configuration.model_aliases[role]
        parameters={"temperature":0,"stream":False,"parallel_tool_calls":False,"tools":[{"type":"function","function":{"name":self._v12.tool_name,"description":"Submit one decision. No action is executed.","parameters":copy.deepcopy(self._v12.tool_schema)}}],"tool_choice":{"type":"function","function":{"name":self._v12.tool_name}}}
        messages=({"role":"system","content":self._prompts[role]},{"role":"user","content":json.dumps(payload,ensure_ascii=False,sort_keys=True)})
        try:response=self._reviewer_client_factory(model).chat(messages,parameters=parameters)
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
        try:decision=json.loads(call.arguments)
        except json.JSONDecodeError:raise self._structure(audit,"arguments_json","reviewer.tool_calls[0].function.arguments") from None
        if not isinstance(decision,Mapping):raise self._structure(audit,"arguments_object","reviewer.tool_calls[0].function.arguments")
        try:validate_reviewer_schema(decision)
        except ReviewerDecisionFailure as exc:raise self._structure(audit,f"schema_{exc.validator}",exc.path) from None
        return dict(decision),audit
    @staticmethod
    def _structure(audit,kind,path):
        code=f"REVIEWER_MODEL_STRUCTURE.{kind}:{path}";return ReviewerChannelFailure(replace(audit,outcome="CONTENT_INVALID",error_code=code),code,repairable=True)

class ThreeRoleRunnerV12(ThreeRoleRunnerV8):
    def __init__(self,*,configuration=None,role_calls=None,output_guard=None):
        config=configuration or load_role_configuration();self._v7_configuration=config;ThreeRoleRunner.__init__(self,configuration=config.roles,role_calls=role_calls or RoleCallAdapterV12(config),output_guard=output_guard or DiagnosticOutputGuardV8())
    def run(self,**kwargs):
        result=super().run(**kwargs)
        if result.status=="DRY_RUN" and result.resolved_intent=="PENDING_TRIAGE":
            rounds=self._configuration.revision_profiles[kwargs.get("revision_profile","production_candidate")]
            reserved=max_provider_calls_v12(rounds,True)
            return replace(result,reserved_provider_calls=reserved,calls=_reserved_calls_v12(self._configuration,rounds,True))
        return result
    def _execute(self,request,plan,review,context,intent,rounds,triage_used,dry_run,calls=None):
        reserved=max_provider_calls_v12(rounds,triage_used)
        if hasattr(self._role_calls,"set_total_limit"):self._role_calls.set_total_limit(reserved)
        if dry_run:return ThreeRoleResult("DRY_RUN",None,intent,0,reserved,_reserved_calls_v12(self._configuration,rounds,triage_used))
        calls=[] if calls is None else calls;actions=[]
        for n in range(rounds+1):
            if hasattr(self._role_calls,"begin_candidate"):self._role_calls.begin_candidate(n)
            candidate,failure=self._executor_candidate(request,plan,review,context,intent,n,actions,calls)
            if failure:return self._fallback(intent,n,reserved,calls,failure)
            try:packet=ReviewerPacket.from_validated_exchange(request=request,plan=plan,review=review,context=context,candidate_output=candidate,resolved_intent=intent,candidate_id=_candidate_id(str(request["request_id"]),n),retry_count=n,config=self._configuration)
            except (ContractValidationError,ValidationError,ValueError,KeyError,TypeError):return self._fallback(intent,n,reserved,calls,"REVIEWER_PACKET_INVALID")
            action,decision,failure,repairable=self._attempt_reviewer(packet,n,rounds,calls,retry=False)
            if failure and repairable:action,decision,failure,_=self._attempt_reviewer(packet,n,rounds,calls,retry=True,safe_reason=_safe_structure_reason(failure))
            if failure:return self._fallback(intent,n,reserved,calls,failure)
            if action is WorkflowAction.FINAL:return ThreeRoleResult("OK",copy.deepcopy(candidate),intent,n,reserved,tuple(calls))
            if action is WorkflowAction.FALLBACK:return self._fallback(intent,n,reserved,calls,"REVIEWER_REJECT_OR_CAP")
            actions=copy.deepcopy(decision["revision_actions"])
        raise AssertionError("loop exhaustion")
    def _attempt_reviewer(self,packet,n,rounds,calls,*,retry,safe_reason=None):
        payload=dict(packet.as_model_input())
        if retry:payload["server_structure_retry"]={"attempt":1,"category":safe_reason}
        try:
            decision,audit=self._role_calls.call_json(role="reviewer",payload=payload);_append(calls,audit);action=next_action(decision,packet=packet,revision_rounds=n,max_revision_rounds=rounds);return action,decision,None,False
        except ReviewerChannelFailure as exc:
            _append(calls,exc.audit);code=("REVIEWER_RETRY"+exc.code.removeprefix("REVIEWER")) if retry else exc.code;calls[-1]=replace(calls[-1],error_code=code);return None,None,code,exc.repairable and not retry
        except RoleCallFailure as exc:_append(calls,exc.audit);return None,None,exc.audit.error_code or "REVIEWER_PROVIDER",False
        except (ContractValidationError,ValidationError,ValueError,KeyError,TypeError):
            code="REVIEWER_SEMANTIC_BINDING.guard:reviewer.decision";calls[-1]=replace(calls[-1],outcome="CONTENT_INVALID",error_code=code);return None,None,code,False

def _safe_structure_reason(code):
    if "arguments_json" in code:return "ARGUMENTS_JSON"
    if "schema_" in code:return "ARGUMENTS_SCHEMA"
    return "TOOL_ENVELOPE"
def _reserved_calls_v12(configuration,rounds,triage):
    calls=[RoleCallAudit(0,"triage",configuration.model_aliases["triage"],"RESERVED")] if triage else []
    for _ in range(rounds+1):calls.extend((RoleCallAudit(0,"executor",configuration.model_aliases["executor"],"RESERVED"),RoleCallAudit(0,"executor",configuration.model_aliases["executor"],"RESERVED"),RoleCallAudit(0,"reviewer",configuration.model_aliases["reviewer"],"RESERVED"),RoleCallAudit(0,"reviewer",configuration.model_aliases["reviewer"],"RESERVED")))
    return tuple(replace(x,attempt_number=i) for i,x in enumerate(calls,1))

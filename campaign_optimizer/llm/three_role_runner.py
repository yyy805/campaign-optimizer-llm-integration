"""Local, provider-ready three-role workflow using canonical v5 contracts."""
from __future__ import annotations

import copy, hashlib, json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from campaign_optimizer.contracts.validation import ContractValidationError, FIXED_NON_OK_ANSWERS, validate_contract_bundle, validate_contract_object
from .agent_workflow_v5 import CONFIG, PROMPTS, ReviewerPacket, RoleConfiguration, WorkflowAction, load_role_configuration, max_provider_calls_with_repairs, next_action, validate_triage_decision
from .intent_policy import HybridIntentPolicy
from .output_guard import OutputGuard
from .qwen_client import QwenClient, QwenClientError, QwenConfig, QwenResponse

class ChatClient(Protocol):
    def chat(self, messages: Sequence[Mapping[str, str]], *, parameters: Mapping[str, Any] | None = None) -> QwenResponse: ...
RoleClientFactory = Callable[[str, str], ChatClient]

@dataclass(frozen=True)
class RoleCallAudit:
    attempt_number: int; role: str; model: str; outcome: str; error_code: str | None = None; request_id: str | None = None; status_code: int | None = None; latency_ms: float | None = None; total_tokens: int | None = None

class RoleCallFailure(RuntimeError):
    def __init__(self, audit: RoleCallAudit, *, repairable: bool) -> None:
        self.audit, self.repairable = audit, repairable; super().__init__(audit.error_code or audit.outcome)

@dataclass(frozen=True)
class ThreeRoleResult:
    status: str; output: Mapping[str, Any] | None; resolved_intent: str; revision_rounds: int; reserved_provider_calls: int; calls: tuple[RoleCallAudit, ...] = field(default_factory=tuple); fallback_reason: str | None = None
    @property
    def provider_calls(self) -> int: return sum(call.outcome != "RESERVED" for call in self.calls)

class RoleCallAdapter:
    """Verified cached prompts prevent hash check/use races and raw-text logging."""
    def __init__(self, configuration: RoleConfiguration, *, client_factory: RoleClientFactory | None = None) -> None:
        self._configuration, self._client_factory = configuration, client_factory or self._default_client_factory
        self._prompts = {role: _load_verified_prompt(configuration, role) for role in configuration.model_aliases}
    @staticmethod
    def _default_client_factory(_: str, model: str) -> ChatClient:
        return QwenClient(replace(QwenConfig.from_env(), model=model))  # lazy; dry-run reads no key
    def call_json(self, *, role: str, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], RoleCallAudit]:
        model = self._configuration.model_aliases[role]
        try:
            response = self._client_factory(role, model).chat(({"role":"system","content":self._prompts[role]}, {"role":"user","content":json.dumps(payload, ensure_ascii=False, sort_keys=True)}), parameters={"temperature":0,"stream":False,"response_format":{"type":"json_object"}})
        except QwenClientError as exc: raise RoleCallFailure(_error_audit(role, model, exc), repairable=False) from exc
        except (OSError, TimeoutError): raise RoleCallFailure(RoleCallAudit(0, role, model, "PROVIDER_ERROR", "NETWORK"), repairable=False) from None
        audit = _response_audit(role, model, response)
        try: parsed = json.loads(response.text)
        except (TypeError, json.JSONDecodeError): raise RoleCallFailure(replace(audit, outcome="INVALID_JSON", error_code="INVALID_JSON"), repairable=True) from None
        if not isinstance(parsed, Mapping): raise RoleCallFailure(replace(audit, outcome="INVALID_JSON", error_code="INVALID_JSON"), repairable=True)
        return dict(parsed), audit

class ThreeRoleRunner:
    """Hard policy -> optional pre-sealing TRIAGE -> Executor/Reviewer loop."""
    def __init__(self, *, configuration: RoleConfiguration | None = None, role_calls: RoleCallAdapter | None = None, output_guard: OutputGuard | None = None) -> None:
        self._configuration = configuration or load_role_configuration(CONFIG); self._role_calls = role_calls or RoleCallAdapter(self._configuration); self._output_guard = output_guard or OutputGuard(); self._hard_router = HybridIntentPolicy()
    def run(self, *, request: Mapping[str, Any], plan: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], question: str | None = None, revision_profile: str = "production_candidate", dry_run: bool = True) -> ThreeRoleResult:
        rounds = self._validate_static(request, plan, review, context, revision_profile)
        # Backend-fixed/previously sealed request: no user query is being routed here.
        if question is None:
            if len(request["allowed_intents"]) != 1: raise ValueError("multi-intent chat requires a server question for triage")
            intent = request["allowed_intents"][0]
            return self._execute(_seal_request(request, intent), plan, review, context, intent, rounds, False, dry_run)
        if question != request["question"]: raise ValueError("router question must exactly match the server-built request")
        routed = self._hard_router.resolve_chat(question)
        if routed.source != "classifier_unavailable":
            if routed.intent not in {"EXPLAIN_PLAN","EXPLAIN_REVIEW","EXPLAIN_RULE"}: return self._refused(routed.intent)
            if routed.intent not in request["allowed_intents"]: return self._fallback("SYSTEM_FALLBACK", 0, 0, (), "ROUTING_INTENT_NOT_ALLOWED")
            return self._execute(_seal_request(request, routed.intent), plan, review, context, routed.intent, rounds, False, dry_run)
        if not _triage_eligible(request): return self._refused("OUT_OF_SCOPE")
        reserved = max_provider_calls_with_repairs(max_revision_rounds=rounds) + 1
        if dry_run: return ThreeRoleResult("DRY_RUN", None, "PENDING_TRIAGE", 0, reserved, _reserved_calls(self._configuration, rounds, True))
        calls: list[RoleCallAudit] = []
        try:
            triage, audit = self._role_calls.call_json(role="triage", payload={"question":question}); _append(calls, audit); validate_triage_decision(triage)
        except RoleCallFailure as exc:
            _append(calls, exc.audit); return self._fallback("SYSTEM_FALLBACK", 0, reserved, calls, exc.audit.error_code or "TRIAGE_INVALID")
        except (ValueError, KeyError, TypeError):
            calls[-1] = replace(calls[-1], outcome="CONTENT_INVALID", error_code="TRIAGE_INVALID"); return self._fallback("SYSTEM_FALLBACK", 0, reserved, calls, "TRIAGE_INVALID")
        if triage["decision"] != "ROUTE": return self._fallback("SYSTEM_FALLBACK", 0, reserved, calls, "TRIAGE_ABSTAIN")
        intent = triage["intent"]
        if intent not in request["allowed_intents"]: return self._fallback("SYSTEM_FALLBACK", 0, reserved, calls, "TRIAGE_INTENT_NOT_ALLOWED")
        return self._execute(_seal_request(request, intent), plan, review, context, intent, rounds, True, False, calls)
    def _execute(self, request: Mapping[str, Any], plan: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], intent: str, rounds: int, triage_used: bool, dry_run: bool, calls: list[RoleCallAudit] | None = None) -> ThreeRoleResult:
        reserved = max_provider_calls_with_repairs(max_revision_rounds=rounds) + int(triage_used)
        if dry_run: return ThreeRoleResult("DRY_RUN", None, intent, 0, reserved, _reserved_calls(self._configuration, rounds, triage_used))
        calls = [] if calls is None else calls; actions: list[Mapping[str, Any]] = []
        for n in range(rounds + 1):
            candidate, failure = self._executor_candidate(request, plan, review, context, intent, n, actions, calls)
            if failure: return self._fallback(intent, n, reserved, calls, failure)
            assert candidate is not None
            try:
                packet = ReviewerPacket.from_validated_exchange(request=request, plan=plan, review=review, context=context, candidate_output=candidate, resolved_intent=intent, candidate_id=_candidate_id(str(request["request_id"]),n), retry_count=n, config=self._configuration)
                decision, audit = self._role_calls.call_json(role="reviewer", payload=packet.as_model_input()); _append(calls, audit); action = next_action(decision, packet=packet, revision_rounds=n, max_revision_rounds=rounds)
            except RoleCallFailure as exc:
                _append(calls, exc.audit); return self._fallback(intent,n,reserved,calls,exc.audit.error_code or "REVIEWER_INVALID")
            except (ContractValidationError, ValueError, KeyError, TypeError):
                calls[-1]=replace(calls[-1], outcome="CONTENT_INVALID",error_code="REVIEWER_INVALID"); return self._fallback(intent,n,reserved,calls,"REVIEWER_INVALID")
            if action is WorkflowAction.FINAL: return ThreeRoleResult("OK",copy.deepcopy(candidate),intent,n,reserved,tuple(calls))
            if action is WorkflowAction.FALLBACK: return self._fallback(intent,n,reserved,calls,"REVIEWER_REJECT_OR_CAP")
            actions=copy.deepcopy(decision["revision_actions"])
        raise AssertionError("loop exhaustion")
    def _validate_static(self, request: Mapping[str, Any], plan: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], profile: str) -> int:
        validate_contract_object("llm_request",dict(request)); validate_contract_bundle(dict(plan),dict(review),dict(context))
        if request["expected_versions"]["prompt_version"] != self._configuration.output_contract_prompt_version: raise ValueError("request output contract does not match pinned role configuration")
        if profile not in self._configuration.revision_profiles: raise ValueError("unknown approved revision profile")
        return self._configuration.revision_profiles[profile]
    def _executor_candidate(self, request: Mapping[str, Any], plan: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], intent: str, n: int, actions: Sequence[Mapping[str, Any]], calls: list[RoleCallAudit]) -> tuple[Mapping[str, Any] | None,str | None]:
        payload=_executor_payload(request,context,intent,n,actions)
        try:
            candidate,audit=self._role_calls.call_json(role="executor",payload=payload); _append(calls,audit); return self._validated(candidate,request,plan,review,context,n),None
        except RoleCallFailure as exc:
            _append(calls,exc.audit)
            if not exc.repairable:return None,exc.audit.error_code or "EXECUTOR_PROVIDER_ERROR"
        except (ContractValidationError,ValueError,KeyError,TypeError): calls[-1]=replace(calls[-1],outcome="CONTENT_INVALID",error_code="EXECUTOR_INVALID")
        repair=copy.deepcopy(payload); repair["server_format_repair"]="Return a complete JSON object conforming to the output contract."
        try:
            candidate,audit=self._role_calls.call_json(role="executor",payload=repair); _append(calls,audit); return self._validated(candidate,request,plan,review,context,n),None
        except RoleCallFailure as exc: _append(calls,exc.audit); return None,exc.audit.error_code or "EXECUTOR_REPAIR_FAILED"
        except (ContractValidationError,ValueError,KeyError,TypeError): calls[-1]=replace(calls[-1],outcome="CONTENT_INVALID",error_code="EXECUTOR_REPAIR_INVALID"); return None,"EXECUTOR_REPAIR_INVALID"
    def _validated(self,candidate: Mapping[str,Any],request:Mapping[str,Any],plan:Mapping[str,Any],review:Mapping[str,Any],context:Mapping[str,Any],n:int)->Mapping[str,Any]:
        return self._output_guard.validate(json.dumps(candidate,ensure_ascii=False),request=dict(request),plan=dict(plan),review=dict(review),context=dict(context),retry_count=n)
    @staticmethod
    def _fallback(intent:str,n:int,reserved:int,calls:Sequence[RoleCallAudit],reason:str)->ThreeRoleResult:return ThreeRoleResult("FALLBACK",_non_ok("FALLBACK","SYSTEM_FALLBACK"),intent,n,reserved,tuple(calls),reason)
    @staticmethod
    def _refused(intent:str)->ThreeRoleResult:return ThreeRoleResult("REFUSED",_non_ok("REFUSED",intent),intent,0,0,(),intent)

def _triage_eligible(request:Mapping[str,Any])->bool:return request["mode"]=="chat" and bool(request["allowed_intents"]) and set(request["allowed_intents"]).issubset({"EXPLAIN_PLAN","EXPLAIN_REVIEW","EXPLAIN_RULE"})
def _seal_request(request:Mapping[str,Any],intent:str)->Mapping[str,Any]:
    result=copy.deepcopy(dict(request));result["allowed_intents"]=[intent];validate_contract_object("llm_request",result);return result
def _non_ok(status:str,intent:str)->Mapping[str,Any]:return {"schema_version":"1.0","workflow_version":"not-published","prompt_version":"draft-1.0","knowledge_base_version":"not-published","status":status,"intent":intent,"answer":FIXED_NON_OK_ANSWERS[intent],"claims":[],"facts_used":[],"rule_ids_used":[],"plan_item_ids_used":[],"limitations_included":False,"retry_count":1 if status=="FALLBACK" else 0,"fallback_used":status=="FALLBACK"}
def _load_verified_prompt(configuration:RoleConfiguration,role:str)->str:
    path=(PROMPTS/f"{configuration.prompt_versions[role]}.md").resolve();raw=path.read_bytes() if path.parent==PROMPTS.resolve() and path.is_file() else None
    if raw is None or hashlib.sha256(raw).hexdigest()!=configuration.prompt_hashes[role]:raise ValueError("approved prompt artifact hash does not match cached bytes")
    return raw.decode("utf-8")
def _executor_payload(request:Mapping[str,Any],context:Mapping[str,Any],intent:str,n:int,actions:Sequence[Mapping[str,Any]])->Mapping[str,Any]:return {"server_task_manifest":{"intent":intent,"expected_versions":copy.deepcopy(request["expected_versions"]),"retry_count":n,"approved_revision_actions":copy.deepcopy(list(actions))},"trusted_context_snapshot":copy.deepcopy(dict(context)),"question":request["question"]}
def _candidate_id(request_id:str,n:int)->str:return f"candidate_{hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:16]}_{n}"
def _response_audit(role:str,model:str,response:QwenResponse)->RoleCallAudit:return RoleCallAudit(0,role,model,"OK",request_id=response.request_id,latency_ms=response.latency_ms,total_tokens=response.usage.total_tokens)
def _error_audit(role:str,model:str,exc:QwenClientError)->RoleCallAudit:return RoleCallAudit(0,role,model,"PROVIDER_ERROR",exc.code.value,exc.request_id,exc.status_code)
def _append(calls:list[RoleCallAudit],audit:RoleCallAudit)->None:calls.append(replace(audit,attempt_number=len(calls)+1))
def _reserved_calls(configuration:RoleConfiguration,rounds:int,triage:bool)->tuple[RoleCallAudit,...]:
    result=[RoleCallAudit(0,"triage",configuration.model_aliases["triage"],"RESERVED")] if triage else []
    for _ in range(rounds+1):result.extend((RoleCallAudit(0,"executor",configuration.model_aliases["executor"],"RESERVED"),RoleCallAudit(0,"reviewer",configuration.model_aliases["reviewer"],"RESERVED"),RoleCallAudit(0,"executor",configuration.model_aliases["executor"],"RESERVED")))
    return tuple(replace(x,attempt_number=i) for i,x in enumerate(result,1))

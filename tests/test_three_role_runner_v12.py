from __future__ import annotations
import copy,json,subprocess,sys
from pathlib import Path
import pytest
from campaign_optimizer.llm.agent_workflow_v12 import BudgetExceeded,BudgetLedgerV12,load_role_configuration,max_provider_calls_v12
from campaign_optimizer.llm.qwen_client import QwenClientError,QwenErrorCode,QwenResponse,QwenUsage
from campaign_optimizer.llm.qwen_function_client_v12 import FunctionResponseV12,ToolCallV12
from campaign_optimizer.llm.three_role_runner_v12 import RoleCallAdapterV12,ThreeRoleRunnerV12
from scripts.run_reviewer_function_pilot_v12 import run_pilot
from scripts.run_three_role_smoke_v12 import serialize_result
ROOT=Path(__file__).parent.parent;FIX=ROOT/"tests"/"fixtures"/"plan_a"
def fixture(n):return json.loads((FIX/n).read_text(encoding="utf-8"))
def qwen(v):return QwenResponse(v if isinstance(v,str) else json.dumps(v),"req","resp","mock",QwenUsage(total_tokens=7),"stop",1.0)
def decision(p,k="PASS"):
 v={"schema_version":"1.0","candidate_id":p["candidate_id"],"packet_digest":p["packet_digest"],"decision":k,"violation_codes":[],"evidence_source_ids":[],"revision_actions":[]}
 if k=="REVISE":v.update({"violation_codes":["MISSING_LIMITATION"],"evidence_source_ids":["review_item_pending"],"revision_actions":[{"operation":"ADD_REQUIRED_LIMITATION","target_claim_id":None,"source_id":"review_item_pending"}]})
 if k=="REJECT":v["violation_codes"]=["UNRESOLVABLE_CONFLICT"]
 return v
def fresp(p,k="PASS",content=None,calls=None,args=None,latency=2,tokens=5):
 if args is None:args=json.dumps(decision(p,k))
 if calls is None:calls=(ToolCallV12("submit_reviewer_decision_v1",args),)
 return FunctionResponseV12(content,tuple(calls),"r","qwen3.7-plus",QwenUsage(total_tokens=tokens),latency,"tool_calls")
class Normal:
 def __init__(self,role,state):self.role,self.state=role,state
 def chat(self,messages,*,parameters=None):
  p=json.loads(messages[1]["content"]);self.state.setdefault("normal_messages",[]).append(messages)
  if self.role=="triage":return qwen({"schema_version":"1.0","agent_role":"TRIAGE","prompt_version":"triage_v2","decision":"ABSTAIN","intent":None,"confidence":.1,"reason_code":"AMBIGUOUS"})
  seq=self.state.get("executor",[])
  if seq:
   x=seq.pop(0)
   if isinstance(x,str):return qwen(x)
  v=fixture("llm_workflow_output.demo.json");v["retry_count"]=p["server_task_manifest"]["retry_count"];return qwen(v)
class Func:
 def __init__(self,state):self.state=state
 def chat(self,messages,*,parameters=None):
  p=json.loads(messages[1]["content"]);self.state.setdefault("reviewer",[]).append((messages,parameters,p));x=self.state["sequence"][min(len(self.state["reviewer"])-1,len(self.state["sequence"])-1)]
  if isinstance(x,Exception):raise x
  return x(p) if callable(x) else x
def run(seq,executor=None,profile="baseline"):
 c=load_role_configuration();s={"sequence":seq,"executor":list(executor or [])};a=RoleCallAdapterV12(c,client_factory=lambda r,m:Normal(r,s),reviewer_client_factory=lambda m:Func(s));result=ThreeRoleRunnerV12(configuration=c,role_calls=a).run(request=fixture("llm_request.demo.json"),plan=fixture("final_plan.demo.json"),review=fixture("ontology_review.demo.json"),context=fixture("llm_context.demo.json"),revision_profile=profile,dry_run=False);return result,s,a
@pytest.mark.parametrize("kind",["PASS","REVISE","REJECT"])
def test_valid_decisions(kind):
 r,s,_=run([lambda p:fresp(p,kind)]);assert r.provider_calls==2 and len(s["reviewer"])==1 and r.status==("OK" if kind=="PASS" else "FALLBACK")
def bad(case,p):
 if case=="text":return fresp(p,content="SECRET")
 if case=="zero":return fresp(p,calls=())
 if case=="multi":return fresp(p,calls=(ToolCallV12("submit_reviewer_decision_v1","{}"),)*2)
 if case=="wrong":return fresp(p,calls=(ToolCallV12("wrong","{}"),))
 if case=="json":return fresp(p,args="SECRET bad")
 if case=="list":return fresp(p,args="[]")
 if case=="extra":v=decision(p);v["SECRET_KEY"]="SECRET_VALUE";return fresp(p,args=json.dumps(v))
@pytest.mark.parametrize("case",["text","zero","multi","wrong","json","list","extra"])
def test_structure_retry_once_no_echo(case):
 r,s,_=run([lambda p:bad(case,p)]);assert r.status=="FALLBACK" and r.provider_calls==3 and len(s["reviewer"])==2;serialized=serialize_result(r)+json.dumps(s["reviewer"][1][2]["server_structure_retry"]);assert "SECRET" not in serialized
@pytest.mark.parametrize("binding",["candidate","digest","evidence","action"])
def test_binding_never_retries(binding):
 def value(p):
  v=decision(p,"REVISE")
  if binding=="candidate":v["candidate_id"]="candidate_SECRET"
  elif binding=="digest":v["packet_digest"]="b"*64
  elif binding=="evidence":v["evidence_source_ids"]=["review_item_SECRET"]
  else:v["revision_actions"][0]["source_id"]="review_item_SECRET"
  return fresp(p,args=json.dumps(v))
 r,s,_=run([value]);assert r.provider_calls==2 and len(s["reviewer"])==1 and r.fallback_reason=="REVIEWER_SEMANTIC_BINDING.guard:reviewer.decision" and "SECRET" not in serialize_result(r)
def test_protocol_zero_retry_explicit_classification():
 r,s,_=run([QwenClientError(QwenErrorCode.INVALID_RESPONSE)]);assert r.provider_calls==2 and len(s["reviewer"])==1 and r.fallback_reason=="REVIEWER_PROTOCOL.invalid_response"
def test_retry_same_packet_tool_schema_and_only_system_user_messages():
 r,s,_=run([lambda p:bad("extra",p),lambda p:fresp(p)]);assert r.status=="OK";m1,par1,p1=s["reviewer"][0];m2,par2,p2=s["reviewer"][1];assert par1==par2 and p1["packet_digest"]==p2["packet_digest"];assert [x["role"] for x in m1]==["system","user"] and [x["role"] for x in m2]==["system","user"]
def test_executor_repair_shared_ledger():
 r,_,a=run([lambda p:fresp(p)],executor=["bad"]);assert r.status=="OK" and a.ledger.by_candidate[0]=={"executor":2,"reviewer":1}
@pytest.mark.parametrize(("n","t","x"),[(0,False,4),(0,True,5),(1,False,8),(3,False,16),(5,True,25)])
def test_budget(n,t,x):assert max_provider_calls_v12(n,t)==x
def test_ledger_caps():
 l=BudgetLedgerV12();l.set_limit(4);l.begin_candidate(0);l.consume("executor");l.consume("executor")
 with pytest.raises(BudgetExceeded):l.consume("executor")
def test_actual_smoke_script_ambiguous_triage_dry_reserves_five():
 completed=subprocess.run([sys.executable,str(ROOT/"scripts"/"run_three_role_smoke_v12.py"),"--profile","baseline","--question","vague request"],cwd=ROOT,capture_output=True,text=True,check=True);data=json.loads(completed.stdout);assert data["status"]=="DRY_RUN" and data["reserved_provider_calls"]==5 and len(data["calls"])==5 and data["calls"][0]["role"]=="triage"
class PilotAdapter:
 def __init__(self,c):self.config=c;self.ledger=BudgetLedgerV12();self.messages=[]
 def begin_candidate(self,n):self.ledger.begin_candidate(n)
 def call_json(self,*,role,payload):
  assert role=="reviewer";self.ledger.consume(role);self.messages.append([{"role":"system"},{"role":"user"}]);d=decision(payload,"PASS" if len(self.messages)<=2 else "REVISE" if len(self.messages)<=4 else "REJECT");return d,type("A",(),{"latency_ms":2.0,"total_tokens":3})()
def test_pilot_mock_only_reviewer_metrics_and_cap():
 c=load_role_configuration();a=PilotAdapter(c);a.ledger.set_limit(10);out=run_pilot(a,c);assert a.ledger.used==5<=10 and out["aggregate"]=={"provider_calls":5,"total_latency_ms":10.0,"total_tokens":15,"matched":5,"case_count":5};assert out["status"]=="PASS" and all([m[0]["role"],m[1]["role"]]==["system","user"] for m in a.messages)

def test_pilot_unknown_exception_is_internal_safe_and_fails_acceptance():
 class InternalAdapter(PilotAdapter):
  def call_json(self,*,role,payload):
   self.ledger.consume(role)
   raise RuntimeError("SECRET_INTERNAL_EXCEPTION_TEXT")
 c=load_role_configuration();a=InternalAdapter(c);a.ledger.set_limit(10);out=run_pilot(a,c);serialized=json.dumps(out)
 assert out["status"]=="FAIL" and out["acceptance"]["accepted"] is False
 assert all(row["failure_category"]=="internal" and row["safe_code"]=="REVIEWER_INTERNAL" for row in out["cases"])
 assert "SECRET_INTERNAL_EXCEPTION_TEXT" not in serialized and "RuntimeError" not in serialized and "traceback" not in serialized.lower()

def test_pilot_known_value_error_is_binding_not_internal():
 class BindingAdapter(PilotAdapter):
  def call_json(self,*,role,payload):
   self.ledger.consume(role)
   raise ValueError("SECRET_BINDING_DETAIL")
 c=load_role_configuration();a=BindingAdapter(c);a.ledger.set_limit(10);out=run_pilot(a,c);serialized=json.dumps(out)
 assert all(row["failure_category"]=="binding" and row["safe_code"]=="REVIEWER_BINDING" for row in out["cases"])
 assert "SECRET_BINDING_DETAIL" not in serialized

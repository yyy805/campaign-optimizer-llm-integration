"""Isolated Reviewer business-judgment eval on the frozen reviewer_judgment_v1 dataset; default dry, no Executor or Triage."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from campaign_optimizer.llm.agent_workflow_v5 import ReviewerPacket
from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration
from campaign_optimizer.llm.request_builder import LLMVersions,RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever
from campaign_optimizer.llm.reviewer_binding_v13 import next_action_v13
from campaign_optimizer.llm.three_role_runner import _candidate_id
from campaign_optimizer.llm.three_role_runner_v12 import _safe_structure_reason
from campaign_optimizer.llm.three_role_runner_v13 import RoleCallAdapterV13
from scripts.run_reviewer_function_pilot_v13 import classify
DATASET=ROOT/"tests"/"fixtures"/"llm_eval"/"reviewer_judgment_v1"
CONFIG_V15=ROOT/"campaign_optimizer"/"llm"/"agent_roles.v15.json"
SCOPE_NOTE="Business-judgment eval on frozen reviewer_judgment_v1 labels; decision match is the hard gate, code membership is reported but not scored."
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def load_cases():
 payload=load(DATASET/"cases.json")
 return tuple((case["case_id"],case["label_class"],case["expected_decision"],frozenset(case["acceptable_violation_codes"]),DATASET/case["candidate_file"]) for case in payload["cases"])
def pending_chain():
 plan=load(ROOT/"tests"/"fixtures"/"plan_a"/"final_plan.demo.json");review=load(DATASET/"ontology_review.pending.json")
 versions=LLMVersions(workflow_version="not-published",prompt_version="draft-1.0",knowledge_base_version="not-published")
 artifacts=RequestBuilder(LocalRuleRetriever(),versions=versions).build(plan,review,mode="initial_render",question="请解释本次推荐方案和本体评价。",resolved_intent="EXPLAIN_REVIEW")
 return plan,review,artifacts
def run_eval(adapter,config,cases):
 plan,review,artifacts=pending_chain();rows=[]
 for index,(case_id,label_class,expected,codes,candidate_path) in enumerate(cases):
  adapter.begin_candidate(index);candidate=load(candidate_path)
  packet=ReviewerPacket.from_validated_exchange(request=artifacts.request,plan=plan,review=review,context=artifacts.context,candidate_output=candidate,resolved_intent="EXPLAIN_REVIEW",candidate_id=_candidate_id(str(artifacts.request["request_id"]),index),retry_count=0,config=config.roles)
  attempts=0;outcome="FALLBACK";category=code=None;latency=0.0;tokens=0;status_code=request_id=None;decision_codes=[];decision_actions=[]
  while attempts<2:
   attempts+=1;payload=dict(packet.as_model_input())
   if attempts==2:payload["server_structure_retry"]={"attempt":1,"category":_safe_structure_reason(code or "")}
   try:
    decision,audit=adapter.call_json(role="reviewer",payload=payload);latency+=audit.latency_ms or 0;tokens+=audit.total_tokens or 0
    decision_codes=list(decision.get("violation_codes",[]));decision_actions=[{"operation":action.get("operation"),"source_id":action.get("source_id"),"target_claim_id":action.get("target_claim_id")} for action in decision.get("revision_actions",[])]
    next_action_v13(decision,packet=packet,revision_rounds=0,max_revision_rounds=0);outcome=decision["decision"];category=code=None;break
   except Exception as exc:
    category,code,repairable=classify(exc)
    from campaign_optimizer.llm.three_role_runner import RoleCallFailure
    from campaign_optimizer.llm.three_role_runner_v12 import ReviewerChannelFailure
    if isinstance(exc,(ReviewerChannelFailure,RoleCallFailure)):status_code=exc.audit.status_code;request_id=exc.audit.request_id
    if isinstance(exc,ReviewerChannelFailure):latency+=exc.audit.latency_ms or 0;tokens+=exc.audit.total_tokens or 0
    if not repairable:break
  match=outcome==expected and code is None;rows.append({"case_id":case_id,"label_class":label_class,"expected":expected,"outcome":outcome,"match":match,"codes_acceptable":set(decision_codes).issubset(codes),"model_violation_codes":decision_codes,"model_revision_actions":decision_actions,"attempts":attempts,"failure_category":category,"safe_code":code,"latency_ms":round(latency,3),"total_tokens":tokens,"status_code":status_code,"request_id":request_id})
 aggregate={"provider_calls":adapter.ledger.used,"total_latency_ms":round(sum(x["latency_ms"] for x in rows),3),"total_tokens":sum(x["total_tokens"] for x in rows),"matched":sum(x["match"] for x in rows),"codes_within_acceptable":sum(x["codes_acceptable"] for x in rows),"case_count":len(rows)}
 accepted=aggregate["matched"]==len(rows) and not any(x["failure_category"] for x in rows)
 return {"status":"PASS" if accepted else "FAIL","acceptance":{"all_expected_decisions_match":aggregate["matched"]==len(rows),"no_structure_or_safety_failure":not any(x["failure_category"] for x in rows),"accepted":accepted},"aggregate":aggregate,"cases":rows,"scope_note":SCOPE_NOTE}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--real",action="store_true");p.add_argument("--case",choices=[case[0] for case in load_cases()]);a=p.parse_args();config=load_role_configuration(CONFIG_V15);cases=tuple(case for case in load_cases() if a.case is None or case[0]==a.case)
 if not a.real:print(json.dumps({"status":"DRY_RUN","case_count":len(cases),"selected_case":a.case,"decision_distribution":{k:sum(1 for case in cases if case[2]==k) for k in ("PASS","REVISE","REJECT")},"label_class_distribution":{k:sum(1 for case in cases if case[1]==k) for k in ("explain_only","refuse_assertion","pending_review_semantics","safety")},"reviewer_call_limit":2*len(cases),"model":config.roles.model_aliases["reviewer"],"tool":config.tool_name,"scope_note":SCOPE_NOTE},sort_keys=True));return 0
 adapter=RoleCallAdapterV13(config);adapter.set_total_limit(2*len(cases));result=run_eval(adapter,config,cases);print(json.dumps(result,sort_keys=True));return 0 if result["acceptance"]["accepted"] else 1
if __name__=="__main__":raise SystemExit(main())

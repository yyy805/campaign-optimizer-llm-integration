"""Reviewer-only capability smoke: one call maximum; default is dry-run."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from campaign_optimizer.llm.agent_workflow_v5 import ReviewerPacket
from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration
from campaign_optimizer.llm.three_role_runner import RoleCallFailure,_candidate_id
from campaign_optimizer.llm.three_role_runner_v12 import ReviewerChannelFailure
from campaign_optimizer.llm.three_role_runner_v13 import RoleCallAdapterV13
from campaign_optimizer.llm.reviewer_http_classifier_v13 import classify_reviewer_http

FIXTURES=ROOT/"tests"/"fixtures"/"plan_a"
def load(name):return json.loads((FIXTURES/name).read_text(encoding="utf-8"))
def safe_result(exc):
    if isinstance(exc,ReviewerChannelFailure):
        return {"status":"FAIL","failure_category":"model_structure","safe_code":exc.code,"status_code":exc.audit.status_code,"request_id":exc.audit.request_id,"latency_ms":exc.audit.latency_ms or 0,"total_tokens":exc.audit.total_tokens or 0}
    if isinstance(exc,RoleCallFailure):
        code=exc.audit.error_code or "PROVIDER"
        category=classify_reviewer_http(code,exc.audit.status_code)
        return {"status":"FAIL","failure_category":category,"safe_code":code,"status_code":exc.audit.status_code,"request_id":exc.audit.request_id,"latency_ms":exc.audit.latency_ms or 0,"total_tokens":exc.audit.total_tokens or 0}
    return {"status":"FAIL","failure_category":"internal","safe_code":"REVIEWER_INTERNAL","status_code":None,"request_id":None,"latency_ms":0,"total_tokens":0}
def run_once(adapter,config):
    request,plan,review,context=(load(x) for x in ("llm_request.demo.json","final_plan.demo.json","ontology_review.demo.json","llm_context.demo.json"))
    candidate=load("llm_workflow_output.demo.json")
    packet=ReviewerPacket.from_validated_exchange(request=request,plan=plan,review=review,context=context,candidate_output=candidate,resolved_intent="EXPLAIN_REVIEW",candidate_id=_candidate_id(str(request["request_id"]),0),retry_count=0,config=config.roles)
    adapter.begin_candidate(0);adapter.set_total_limit(1)
    try:
        _,audit=adapter.call_json(role="reviewer",payload=dict(packet.as_model_input()))
        return {"status":"PASS","failure_category":None,"safe_code":None,"status_code":audit.status_code,"request_id":audit.request_id,"latency_ms":audit.latency_ms or 0,"total_tokens":audit.total_tokens or 0}
    except Exception as exc:return safe_result(exc)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--real",action="store_true");a=p.parse_args();config=load_role_configuration()
    if not a.real:print(json.dumps({"status":"DRY_RUN","provider_call_limit":1,"automatic_retry":False,"enable_thinking":False,"model":config.roles.model_aliases["reviewer"],"next_step":"Run --real once; run the five-case pilot separately only after PASS."},sort_keys=True));return 0
    result=run_once(RoleCallAdapterV13(config),config);print(json.dumps(result,sort_keys=True));return 0 if result["status"]=="PASS" else 1
if __name__=="__main__":raise SystemExit(main())


"""Three-role live E2E on the canonical pending context; default dry, v15 config, no knowledge base dependency."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration,max_provider_calls_v12
from campaign_optimizer.llm.request_builder import LLMVersions,RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever
from campaign_optimizer.llm.three_role_runner_v13 import RoleCallAdapterV13,ThreeRoleRunnerV13
CONFIG_V15=ROOT/"campaign_optimizer"/"llm"/"agent_roles.v15.json"
DATASET=ROOT/"tests"/"fixtures"/"llm_eval"/"reviewer_judgment_v1"
DEFAULT_QUESTION="请解释本次推荐方案和本体评价。"
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def pending_chain(mode,question):
 plan=load(ROOT/"tests"/"fixtures"/"plan_a"/"final_plan.demo.json");review=load(DATASET/"ontology_review.pending.json")
 versions=LLMVersions(workflow_version="not-published",prompt_version="draft-1.0",knowledge_base_version="not-published")
 artifacts=RequestBuilder(LocalRuleRetriever(),versions=versions).build(plan,review,mode=mode,question=question,resolved_intent="EXPLAIN_REVIEW")
 return plan,review,artifacts
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--real",action="store_true");p.add_argument("--chat",action="store_true",help="use chat mode so triage runs too");a=p.parse_args()
 config=load_role_configuration(CONFIG_V15);question=DEFAULT_QUESTION
 plan,review,artifacts=pending_chain("chat" if a.chat else "initial_render",question)
 triage=a.chat;limit=max_provider_calls_v12(0,triage)
 if not a.real:print(json.dumps({"status":"DRY_RUN","mode":"chat" if triage else "initial_render","revision_profile":"baseline","provider_call_limit":limit,"reviewer_prompt":config.roles.prompt_versions["reviewer"]},sort_keys=True));return 0
 adapter=RoleCallAdapterV13(config);adapter.set_total_limit(limit)
 runner=ThreeRoleRunnerV13(configuration=config,role_calls=adapter)
 kwargs={"request":artifacts.request,"plan":plan,"review":review,"context":artifacts.context,"revision_profile":"baseline","dry_run":False}
 result=runner.run(question=question if triage else None,**kwargs)
 calls=[{"attempt":c.attempt_number,"role":c.role,"model":c.model,"outcome":c.outcome,"error_code":c.error_code,"latency_ms":c.latency_ms,"total_tokens":c.total_tokens} for c in result.calls]
 out={"status":result.status,"resolved_intent":result.resolved_intent,"revision_rounds":result.revision_rounds,"provider_calls":result.provider_calls,"fallback_reason":result.fallback_reason,"calls":calls}
 if result.output is not None:out["answer"]=result.output["answer"];out["limitations_included"]=result.output["limitations_included"]
 print(json.dumps(out,ensure_ascii=False,sort_keys=True));return 0 if result.status=="OK" else 1
if __name__=="__main__":raise SystemExit(main())

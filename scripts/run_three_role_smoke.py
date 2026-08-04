"""Run the local three-role dry-run; add --real only for an intentional paid smoke."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from campaign_optimizer.llm.three_role_runner import ThreeRoleRunner
FIXTURES=ROOT/"tests"/"fixtures"/"plan_a"
def load(name):return json.loads((FIXTURES/name).read_text(encoding="utf-8"))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--real",action="store_true");p.add_argument("--profile",default="baseline",choices=("baseline","production_candidate","experiment","stress_only"));p.add_argument("--question");a=p.parse_args();request=load("llm_request.demo.json")
 if a.question is not None:request.update({"mode":"chat","question":a.question,"allowed_intents":["EXPLAIN_PLAN","EXPLAIN_REVIEW","EXPLAIN_RULE"]})
 r=ThreeRoleRunner().run(request=request,plan=load("final_plan.demo.json"),review=load("ontology_review.demo.json"),context=load("llm_context.demo.json"),question=a.question,revision_profile=a.profile,dry_run=not a.real)
 print(json.dumps({"status":r.status,"resolved_intent":r.resolved_intent,"revision_rounds":r.revision_rounds,"reserved_provider_calls":r.reserved_provider_calls,"provider_calls":r.provider_calls,"fallback_reason":r.fallback_reason,"calls":[x.__dict__ for x in r.calls]},ensure_ascii=False,sort_keys=True));return 0 if r.status in {"DRY_RUN","OK","REFUSED"} else 1
if __name__=="__main__":raise SystemExit(main())

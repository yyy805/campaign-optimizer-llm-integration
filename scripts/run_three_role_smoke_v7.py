"""Run the v7 local three-role smoke; add --real only intentionally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from campaign_optimizer.llm.three_role_runner_v7 import ThreeRoleRunnerV7

FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--profile", default="baseline", choices=("baseline", "production_candidate", "experiment", "stress_only"))
    parser.add_argument("--question")
    args = parser.parse_args()
    request = load("llm_request.demo.json")
    if args.question is not None:
        request.update({"mode": "chat", "question": args.question, "allowed_intents": ["EXPLAIN_PLAN", "EXPLAIN_REVIEW", "EXPLAIN_RULE"]})
    result = ThreeRoleRunnerV7().run(
        request=request,
        plan=load("final_plan.demo.json"),
        review=load("ontology_review.demo.json"),
        context=load("llm_context.demo.json"),
        question=args.question,
        revision_profile=args.profile,
        dry_run=not args.real,
    )
    print(json.dumps({"status": result.status, "resolved_intent": result.resolved_intent, "revision_rounds": result.revision_rounds, "reserved_provider_calls": result.reserved_provider_calls, "provider_calls": result.provider_calls, "fallback_reason": result.fallback_reason, "calls": [item.__dict__ for item in result.calls]}, ensure_ascii=False, sort_keys=True))
    return 0 if result.status in {"DRY_RUN", "OK", "REFUSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

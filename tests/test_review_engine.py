from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.review_engine import generate_ontology_review

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"
RELEASE_IDENTITY = {
    'ontology_version': '2.0-campaign-pending',
    'rule_version': 'R5@2.0-campaign-pending',
    'engine_version': '2.0',
    'schema_version': '1.1',
    'source_commit': 'a' * 40,
    'package_checksum': 'b' * 64,
}


def _plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _generate(plan: dict, **kwargs) -> dict:
    return generate_ontology_review(
        plan,
        ontology_version="2.0-campaign-pending",
        confidence_state_version="unprovisioned",
        release_identity=kwargs.pop('release_identity', RELEASE_IDENTITY),
        **kwargs,
    )


def test_pending_campaign_r5_never_issues_decisive_verdict():
    review = _generate(_plan())
    assert review['release_identity'] == RELEASE_IDENTITY
    assert review["overall_verdict"] == "UNVERIFIED"
    assert review["items"][0]["verdict"] == "UNVERIFIED"
    assert review["items"][0]["rule_id"] is None


def test_release_identity_must_match_review_ontology_version():
    identity = {**RELEASE_IDENTITY, 'ontology_version': 'wrong'}
    with pytest.raises(ContractValidationError, match='release identity'):
        _generate(_plan(), release_identity=identity)


def test_pending_r5_cannot_be_explicitly_enabled():
    with pytest.raises(ContractValidationError, match="must be ACTIVE"):
        _generate(_plan(), enabled_rule_ids=("R5",))


def test_confidence_for_disabled_rule_fails_closed():
    with pytest.raises(ContractValidationError, match="disabled rules: R5"):
        _generate(_plan(), confidence_states={"R5": {"status": "ACTIVE"}})


def test_rule_outside_implemented_scope_is_rejected():
    with pytest.raises(ContractValidationError, match="does not implement: R3"):
        _generate(_plan(), enabled_rule_ids=("R3",))


def test_duplicate_enabled_rule_is_rejected():
    with pytest.raises(ContractValidationError, match="must be unique"):
        _generate(_plan(), enabled_rule_ids=("R5", "R5"))


def test_plan_integrity_is_checked_even_without_active_rules():
    plan = _plan()
    plan["items"][0]["recommended_budget"] = math.nan
    with pytest.raises(ContractValidationError, match="must be finite"):
        _generate(plan)


def test_fact_entity_mismatch_is_rejected_before_review():
    plan = _plan()
    plan["review_evidence"][0]["entity_id"] = "another-entity"
    with pytest.raises(ContractValidationError, match="entity does not match"):
        _generate(plan)


def test_duplicate_review_concepts_are_rejected_before_review():
    plan = _plan()
    duplicate = copy.deepcopy(plan["review_evidence"][0])
    duplicate["fact_id"] = "review_fact_duplicate"
    plan["review_evidence"].append(duplicate)
    with pytest.raises(ContractValidationError, match="duplicate review concepts"):
        _generate(plan)


def test_generation_is_deterministic_and_does_not_mutate_plan():
    plan = _plan()
    before = copy.deepcopy(plan)
    assert _generate(plan) == _generate(plan)
    assert plan == before

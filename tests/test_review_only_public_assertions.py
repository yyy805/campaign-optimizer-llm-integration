from __future__ import annotations

import json
from pathlib import Path

from campaign_optimizer.contracts.validation import validate_contract_bundle

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_review_only_assertion_catalog_is_explicit_and_rule_independent():
    suite = _load(
        ROOT / "campaign_optimizer/ontology/assertions/review_only_contract.json"
    )
    assert suite["rule_card_independent"] is False
    assert {item["id"] for item in suite["assertions"]} == {
        "REV-001", "REV-002", "REV-003", "REV-004",
        "REV-005", "REV-006", "REV-007", "REV-008", "REV-009",
        "REV-010", "REV-011", "REV-012", "REV-013", "REV-014",
    }


def test_demo_bundle_satisfies_review_only_public_contract_without_loading_rules():
    validate_contract_bundle(
        _load(FIXTURES / "final_plan.demo.json"),
        _load(FIXTURES / "ontology_review.demo.json"),
        _load(FIXTURES / "llm_context.demo.json"),
    )

from __future__ import annotations

import json
import shutil

import pytest

from app.ontology import OntologyLoadError, load_ontology
from tests.conftest import ONTOLOGY_ROOT


def test_loads_complete_immutable_canonical_package():
    snapshot = load_ontology(ONTOLOGY_ROOT)
    assert snapshot.version == "v1.1-demo"
    assert len(snapshot.checksum) == 64
    assert set(snapshot.rules) == {f"R{number}" for number in range(1, 8)}
    assert snapshot.rules["R7"]["status"] == "RETIRED"
    assert set(snapshot.guardrails) == {"G1", "G2"}
    with pytest.raises(TypeError):
        snapshot.rules["R1"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.rules["R1"]["recommended_action"]["type"] = "mutated"  # type: ignore[index]


def test_checksum_is_deterministic():
    assert load_ontology(ONTOLOGY_ROOT).checksum == load_ontology(ONTOLOGY_ROOT).checksum


def test_rejects_broken_rule_package(tmp_path):
    target = tmp_path / "ontology"
    shutil.copytree(ONTOLOGY_ROOT, target)
    rule_path = target / "rules" / "R1.json"
    rule = json.loads(rule_path.read_text(encoding="utf-8"))
    rule["status"] = "NOT_A_STATUS"
    rule_path.write_text(json.dumps(rule), encoding="utf-8")
    with pytest.raises(OntologyLoadError, match="NOT_A_STATUS"):
        load_ontology(target)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("inf")])
def test_rejects_invalid_client_risk_thresholds(tmp_path, value):
    target = tmp_path / "ontology"
    shutil.copytree(ONTOLOGY_ROOT, target)
    client_path = target / "clients" / "demo_client_001.json"
    client = json.loads(client_path.read_text(encoding="utf-8"))
    client["risk_tolerance"]["max_auto_budget_change_pct"] = value
    client_path.write_text(json.dumps(client), encoding="utf-8")
    with pytest.raises(OntologyLoadError, match="max_auto_budget_change_pct"):
        load_ontology(target)


@pytest.mark.parametrize("value_range", [[2, 1], [0, float("inf")], ["low", 1]])
def test_rejects_invalid_concept_value_range_bounds(tmp_path, value_range):
    target = tmp_path / "ontology"
    shutil.copytree(ONTOLOGY_ROOT, target)
    concept_path = target / "concepts" / "acos.json"
    concept = json.loads(concept_path.read_text(encoding="utf-8"))
    concept["value_range"] = value_range
    concept_path.write_text(json.dumps(concept), encoding="utf-8")
    with pytest.raises(OntologyLoadError, match="value_range"):
        load_ontology(target)

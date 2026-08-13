"""Offline gates for the lightweight RAG v1 export and retrieval question set."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from campaign_optimizer.llm.release_pin import load_verified_manifests, release_identity
from scripts.export_knowledge_base_v1 import build_manifest, export_documents

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "campaign_optimizer" / "ontology" / "rules"
QUESTIONS = ROOT / "tests" / "fixtures" / "kb_retrieval_v1" / "questions.json"
FROZEN_QUESTION_COUNT = 12


def test_export_is_deterministic():
    identity_a, documents_a = export_documents()
    identity_b, documents_b = export_documents()
    assert identity_a == identity_b
    assert [d["content"] for d in documents_a] == [d["content"] for d in documents_b]
    assert build_manifest(identity_a, documents_a) == build_manifest(identity_b, documents_b)


def test_every_document_is_a_faithful_projection_with_explicit_status():
    _, documents = export_documents()
    assert {d["rule_id"] for d in documents} == {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}
    for doc in documents:
        card = json.loads((RULES / f"{doc['rule_id']}.json").read_text(encoding="utf-8"))
        assert f"rule_id: {doc['rule_id']}\n" in doc["content"]
        assert f"rule_version: {doc['rule_version']}\n" in doc["content"]
        assert f"status: {doc['status']}\n" in doc["content"]
        assert doc["status"] == card["status"]
        assert doc["rule_version"] == card["version_history"][-1]["version"]
        body = doc["content"].split("---\n", 2)[2]
        assert json.loads(body) == card


def test_manifest_pins_current_release_identity_and_checksums():
    identity, documents = export_documents()
    manifest = build_manifest(identity, documents)
    current = next(v for v in load_verified_manifests().values() if v["ontology_version"] == "2.0-campaign-pending")
    assert manifest["release_identity"] == release_identity(current)
    assert manifest["similarity_threshold"] == 0.6
    for entry, doc in zip(manifest["documents"], documents):
        assert entry["sha256"] == hashlib.sha256(doc["content"].encode("utf-8")).hexdigest()


def test_pending_and_retired_statuses_are_explicit():
    _, documents = export_documents()
    by_id = {d["rule_id"]: d for d in documents}
    assert by_id["R5"]["status"] == "PENDING_HUMAN_REVIEW"
    assert by_id["R7"]["status"] == "RETIRED"
    assert "PENDING_HUMAN_REVIEW" in by_id["R5"]["content"]


def test_question_set_is_frozen_and_consistent():
    payload = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    questions = payload["questions"]
    assert payload["suite_id"] == "kb-retrieval-v1"
    assert len(questions) == FROZEN_QUESTION_COUNT
    ids = [q["question_id"] for q in questions]
    assert len(set(ids)) == len(ids)
    rule_ids = {f"R{i}" for i in range(1, 8)}
    no_result = 0
    for q in questions:
        assert sum(key in q for key in ("expected_rule_ids", "expected_rule_ids_within", "expected_no_result")) == 1
        targets = q.get("expected_rule_ids") or q.get("expected_rule_ids_within") or []
        assert set(targets) <= rule_ids
        if q.get("expected_no_result"):
            no_result += 1
    assert no_result >= 1
    assert any(q.get("expected_rule_ids") == ["R5"] for q in questions)
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    for marker in ("dashscope_api_key", "authorization: bearer", "customer_email"):
        assert marker not in serialized

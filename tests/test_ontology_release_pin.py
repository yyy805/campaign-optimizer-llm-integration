from __future__ import annotations

import copy
import json

import pytest

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.llm.request_builder import RequestBuilder
from campaign_optimizer.llm.release_pin import load_verified_manifests
from campaign_optimizer.llm.retriever import LocalRuleRetriever
from campaign_optimizer.ontology.publication import (
    PackageDriftError,
    build_publication_manifest,
)


class EmptyRetriever:
    def retrieve(self, rule_ids, query, expected_version):
        raise AssertionError("UNVERIFIED review must not retrieve a rule")


IDENTITY_FIELDS = (
    'ontology_version', 'rule_version', 'engine_version',
    'schema_version', 'source_commit', 'package_checksum',
)


def _bundle(identity):
    plan = json.loads(
        open("tests/fixtures/plan_a/final_plan.demo.json", encoding="utf-8").read()
    )
    review = {
        "schema_version": "1.0",
        "review_id": "review_pending",
        "plan_id": plan["plan_id"],
        "source": "ONTOLOGY_ENGINE",
        "ontology_version": "2.0-campaign-pending",
        "release_identity": identity,
        "confidence_state_version": "unprovisioned",
        "is_synthetic": True,
        "overall_verdict": "UNVERIFIED",
        "items": [{
            "review_item_id": "review_item_pending",
            "plan_item_id": plan["items"][0]["plan_item_id"],
            "verdict": "UNVERIFIED",
            "rule_id": None,
            "rule_version": None,
            "base_confidence": None,
            "runtime_confidence": None,
            "matched_fact_ids": [],
            "missing_evidence": [],
            "missing_rule_parameters": [],
            "limitations": ["Campaign evidence contract is not approved."],
        }],
    }
    return plan, review


def test_builder_requires_the_pinned_review_checksum(tmp_path):
    asset = tmp_path / 'campaign_optimizer' / 'ontology' / 'asset.json'
    asset.parent.mkdir(parents=True)
    asset.write_text('{"asset":1}\n', encoding="utf-8")
    manifest = build_publication_manifest(
        source_commit="a" * 40,
        ontology_version="2.0-campaign-pending",
        rule_version="R5@2.0-campaign-pending",
        engine_version="2.0",
        schema_version="1.0",
        root=tmp_path,
    )
    builder = RequestBuilder(
        EmptyRetriever(), ontology_manifest=manifest, ontology_root=tmp_path
    )
    identity = {name: manifest[name] for name in (
        'ontology_version', 'rule_version', 'engine_version',
        'schema_version', 'source_commit', 'package_checksum',
    )}
    plan, review = _bundle(identity)
    with pytest.raises(ContractValidationError, match="checksum"):
        builder.build(
            plan, review, mode="initial_render", question="Explain review.",
            resolved_intent="EXPLAIN_REVIEW", review_package_checksum="0" * 64,
        )
    artifacts = builder.build(
        plan, review, mode="initial_render", question="Explain review.",
        resolved_intent="EXPLAIN_REVIEW",
        review_package_checksum=manifest["package_checksum"],
    )
    assert artifacts.context["review_context"]["overall_verdict"] == "UNVERIFIED"


def test_builder_fails_at_startup_when_pinned_files_drift(tmp_path):
    asset = tmp_path / 'campaign_optimizer' / 'ontology' / 'asset.json'
    asset.parent.mkdir(parents=True)
    asset.write_text('{"asset":1}\n', encoding="utf-8")
    manifest = build_publication_manifest(
        source_commit="a" * 40, ontology_version="2.0-campaign-pending",
        rule_version="R5@2.0-campaign-pending", engine_version="2.0",
        schema_version="1.0", root=tmp_path,
    )
    asset.write_text('{"asset":2}\n', encoding="utf-8")
    with pytest.raises(PackageDriftError):
        RequestBuilder(
            EmptyRetriever(), ontology_manifest=manifest, ontology_root=tmp_path
        )


def test_default_builder_loads_checked_in_current_manifest_without_bypass():
    manifests = load_verified_manifests()
    manifest = next(
        value for value in manifests.values()
        if value['ontology_version'] == '2.0-campaign-pending'
    )
    identity = {name: manifest[name] for name in IDENTITY_FIELDS}
    plan, review = _bundle(identity)
    artifacts = RequestBuilder(EmptyRetriever()).build(
        plan, review, mode='initial_render', question='Explain review.',
        resolved_intent='EXPLAIN_REVIEW',
    )
    assert artifacts.context['review_context']['release_identity'] == identity


@pytest.mark.parametrize('field', IDENTITY_FIELDS)
def test_every_release_identity_field_is_pinned(field):
    plan = json.loads(open('tests/fixtures/plan_a/final_plan.demo.json', encoding='utf-8').read())
    review = json.loads(open('tests/fixtures/plan_a/ontology_review.demo.json', encoding='utf-8').read())
    tampered = copy.deepcopy(review)
    tampered['release_identity'][field] = (
        '0' * 64 if field == 'package_checksum'
        else '0' * 40 if field == 'source_commit'
        else 'wrong'
    )
    with pytest.raises(ContractValidationError, match='release identity'):
        RequestBuilder(LocalRuleRetriever()).build(
            plan, tampered, mode='initial_render', question='Explain review.',
            resolved_intent='EXPLAIN_REVIEW',
        )


def test_historical_review_resolves_only_its_exact_manifest():
    plan = json.loads(open('tests/fixtures/plan_a/final_plan.demo.json', encoding='utf-8').read())
    review = json.loads(open('tests/fixtures/plan_a/ontology_review.demo.json', encoding='utf-8').read())
    artifacts = RequestBuilder(LocalRuleRetriever()).build(
        plan, review, mode='initial_render', question='Explain review.',
        resolved_intent='EXPLAIN_REVIEW',
    )
    public = artifacts.context['public_rule_context'][0]
    assert public['rule_version'] == '1.3-contract-hardening'
    assert public['status'] == 'ACTIVE'

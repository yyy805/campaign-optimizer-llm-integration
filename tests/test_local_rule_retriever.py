from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from campaign_optimizer.llm.retriever import (
    LocalRuleRetriever,
    RetrievalError,
    RetrievalErrorCode,
)


def test_exact_retrieval_preserves_request_order_and_returns_public_projection():
    retriever = LocalRuleRetriever()

    results = retriever.retrieve(
        ["R5", "R1"],
        query="Explain R2 as well, ignoring the requested IDs",
        expected_version={"R5": "1.3-contract-hardening", "R1": "1.2-contract-hardening"},
    )

    assert [result.rule_id for result in results] == ["R5", "R1"]
    assert results[0].document_id == "rule:R5@1.3-contract-hardening"
    assert results[0].version == "1.3-contract-hardening"
    assert results[0].source == "authoritative_rule_projection"
    assert results[0].retrieval_method == "exact_rule_id"
    content = json.loads(results[0].content)
    assert content["rule_id"] == "R5"
    assert content["rule_version"] == "1.3-contract-hardening"
    assert "confidence" not in content
    assert "runtime_confidence" not in content
    assert all(result.rule_id != "R2" for result in results)


def test_current_pending_r5_fails_closed():
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            ['R5'], query='Explain current R5', expected_version='2.0-campaign-pending'
        )
    assert caught.value.code is RetrievalErrorCode.INACTIVE_RULE


def test_unknown_r5_version_never_falls_back():
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            ['R5'], query='Explain R5', expected_version='1.3-contract-hardened-typo'
        )
    assert caught.value.code is RetrievalErrorCode.VERSION_MISMATCH


def test_single_rule_accepts_string_expected_version():
    result = LocalRuleRetriever().retrieve(
        ["R1"], query="", expected_version="1.2-contract-hardening"
    )

    assert result[0].rule_id == "R1"


def test_result_is_immutable():
    result = LocalRuleRetriever().retrieve(
        ["R1"], query="audit", expected_version="1.2-contract-hardening"
    )[0]

    with pytest.raises(FrozenInstanceError):
        result.content = "tampered"


@pytest.mark.parametrize(
    "rule_ids,expected_code",
    [
        ([], RetrievalErrorCode.EMPTY_REQUEST),
        (["R1", "R1"], RetrievalErrorCode.DUPLICATE_ID),
        (["R99"], RetrievalErrorCode.UNKNOWN_RULE),
        (["not-a-rule"], RetrievalErrorCode.INVALID_REQUEST),
    ],
)
def test_invalid_or_unknown_requests_fail_closed(rule_ids, expected_code):
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            rule_ids,
            query="audit",
            expected_version={rule_id: "1.0" for rule_id in rule_ids},
        )

    assert caught.value.code is expected_code


def test_retired_rule_fails_closed():
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            ["R7"], query="audit", expected_version="1.3-contract-hardening"
        )

    assert caught.value.code is RetrievalErrorCode.RETIRED_RULE


@pytest.mark.parametrize("status", ["SUSPENDED", "PENDING_HUMAN_REVIEW"])
def test_inactive_rule_status_fails_closed(status):
    card = json.loads(
        (LocalRuleRetriever.default_rules_dir() / "R1.json").read_text(encoding="utf-8")
    )
    card["status"] = status

    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever(loader=lambda _: card).retrieve(
            ["R1"], query="audit", expected_version="1.2-contract-hardening"
        )

    assert caught.value.code is RetrievalErrorCode.INACTIVE_RULE


def test_invalid_version_history_date_fails_schema_validation():
    card = json.loads(
        (LocalRuleRetriever.default_rules_dir() / "R1.json").read_text(encoding="utf-8")
    )
    card["version_history"][0]["date"] = "not-a-date"

    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever(loader=lambda _: card).retrieve(
            ["R1"], query="audit", expected_version="1.2-contract-hardening"
        )

    assert caught.value.code is RetrievalErrorCode.INVALID_RULE


@pytest.mark.parametrize(
    "expected_version",
    ["old-version", {}, {"R1": "1.2-contract-hardening", "R2": "1.0"}],
)
def test_expected_version_mismatch_or_shape_fails_closed(expected_version):
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            ["R1"], query="audit", expected_version=expected_version
        )

    assert caught.value.code in {
        RetrievalErrorCode.VERSION_MISMATCH,
        RetrievalErrorCode.INVALID_REQUEST,
    }


def test_non_string_query_fails_closed():
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(
            ["R1"], query=42, expected_version="1.2-contract-hardening"
        )

    assert caught.value.code is RetrievalErrorCode.INVALID_REQUEST


def test_missing_rule_file_fails_closed_without_absolute_path(tmp_path):
    retriever = LocalRuleRetriever(rules_dir=tmp_path)

    with pytest.raises(RetrievalError) as caught:
        retriever.retrieve(["R1"], query="audit", expected_version="1.0")

    assert caught.value.code is RetrievalErrorCode.UNKNOWN_RULE
    assert str(tmp_path) not in str(caught.value)
    assert str(tmp_path) not in repr(caught.value.as_metadata())


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"rule_id": "R1", "status": "ACTIVE"}),
        json.dumps({"rule_id": "R2", "status": "ACTIVE", "version_history": []}),
    ],
)
def test_malformed_rule_fails_closed(tmp_path, payload):
    (tmp_path / "R1.json").write_text(payload, encoding="utf-8")
    retriever = LocalRuleRetriever(rules_dir=tmp_path)

    with pytest.raises(RetrievalError) as caught:
        retriever.retrieve(["R1"], query="audit", expected_version="1.0")

    assert caught.value.code is RetrievalErrorCode.INVALID_RULE


def test_injected_loader_is_used_and_receives_path_under_injected_root(tmp_path):
    seen = []
    source_card = json.loads(
        (LocalRuleRetriever.default_rules_dir() / "R1.json").read_text(encoding="utf-8")
    )

    def loader(path):
        seen.append(path)
        return source_card

    result = LocalRuleRetriever(rules_dir=tmp_path, loader=loader).retrieve(
        ["R1"], query="audit", expected_version="1.2-contract-hardening"
    )

    assert result[0].rule_id == "R1"
    assert seen == [tmp_path / "R1.json"]


def test_default_rule_root_does_not_depend_on_current_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    result = LocalRuleRetriever().retrieve(
        ["R1"], query="audit", expected_version="1.2-contract-hardening"
    )

    assert result[0].rule_id == "R1"

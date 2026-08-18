from __future__ import annotations

import hashlib
import json
from pathlib import Path

from campaign_optimizer.ontology.publication import INCLUDED_PREFIXES


ROOT = Path(__file__).parent.parent
ONTOLOGY = ROOT / "campaign_optimizer" / "ontology"
EXPECTED_R5_SHA256 = "eced62fd789b0fb903a50722fe4600ea06906a357af88c84f023122292eb7b64"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_columns(filename: str) -> set[str]:
    manifest = _json(ONTOLOGY / "mta" / "field_manifest.json")
    matches = [
        set(spec["required_columns"])
        for spec in manifest["files"]
        if Path(spec["path"]).name == filename
    ]
    assert len(matches) == 1
    return matches[0]


def test_contribution_share_maps_to_governed_official_touchpoint_output():
    card = _json(ONTOLOGY / "concepts" / "contribution_share.json")
    columns = _manifest_columns("amc_mta_recommended_attribution.csv")

    assert {"official_share", "official_model", "outcome", "touchpoint"} <= columns
    assert card["source_field"] == "official_share"
    assert card["unit"] == "ratio"
    assert card["granularity"] == {
        "time": "snapshot",
        "entity": "touchpoint",
        "aggregation": "none",
        "requires_full_batch": True,
    }
    assert card["dimensions"] == [
        "report_window",
        "touchpoint",
        "outcome",
        "official_model",
    ]
    assert "official_model=MARKOV" in card["definition"]
    assert "NO_COVERAGE" in card["definition"]


def test_attribution_divergence_uses_absolute_gap_not_relative_gap():
    card = _json(ONTOLOGY / "concepts" / "attribution_divergence.json")
    columns = _manifest_columns("amc_mta_model_comparison_touchpoints.csv")

    assert {"gap_pp", "markov_share", "shapley_share", "outcome", "touchpoint"} <= columns
    assert card["source_field"] == "gap_pp"
    assert card["formula"] == "gap_pp / 100"
    assert card["unit"] == "ratio"
    assert card["dimensions"] == ["report_window", "touchpoint", "outcome"]
    assert "relative_gap" not in card["formula"]
    assert "Campaign" not in card["definition"]


def test_spend_share_explicitly_refuses_r5_use_without_producer_contract():
    card = _json(ONTOLOGY / "concepts" / "spend_share.json")

    assert card["formula"] == "spend / total_spend"
    assert card["granularity"]["time"] == "daily"
    assert card["granularity"]["entity"] == "campaign"
    assert "R5" in card["definition"]
    assert "NO_COVERAGE" in card["definition"]
    assert "尚未批准" in card["definition"]


def test_candidate_fields_are_documented_outside_publication_roots():
    document = ROOT / "docs" / "ontology" / "contracts" / "r5-v2-candidate-evidence-fields.md"
    content = document.read_text(encoding="utf-8")
    relative_document = document.relative_to(ROOT)

    assert all(
        not relative_document.is_relative_to(prefix)
        for prefix in INCLUDED_PREFIXES
    )
    publication_manifest = _json(ONTOLOGY / "publication_manifest.json")
    assert relative_document.as_posix() not in {
        entry["path"] for entry in publication_manifest["entries"]
    }
    assert not list((ROOT / ".ontology_bundles").rglob(document.name))
    for field in (
        "campaign_revenue_contribution_share",
        "campaign_spend_share",
        "campaign_attribution_evidence_reliable",
        "bridge_fallback_used",
        "evidence_batch_id",
        "source_artifact_hash",
    ):
        assert f"`{field}`" in content
    assert "non-authoritative" in content
    assert "modules/amc_mta" in content
    assert "PENDING_HUMAN_REVIEW" in content


def test_r5_remains_byte_identical_and_pending():
    path = ONTOLOGY / "rules" / "R5.json"
    raw = path.read_bytes()
    card = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == EXPECTED_R5_SHA256
    assert card["status"] == "PENDING_HUMAN_REVIEW"
    assert card["trigger_condition"] == {"all": []}
    assert card["review_policy"]["supported_plan_actions"] == []
    assert card["review_policy"]["conflicting_plan_actions"] == []

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_optimizer.llm.release_pin import bundle_root
from campaign_optimizer.ontology.publication import PackageDriftError

from app.services.plan_review_service import PlanReviewService


PINNED_CHECKSUM = "f10e335d47387b527044e9429a2b316d99fcda7af0ae2495ba0ff138eafa9d0c"


def test_plan_review_service_binds_identity_and_assets_to_frozen_bundle(tmp_path: Path):
    service = PlanReviewService(f"sqlite:///{tmp_path / 'review.db'}", "demo_client_001")

    assert service.release_root == bundle_root(service.manifest)
    assert service.release_root != service.project_root
    assert service.workflow.release.package_checksum == PINNED_CHECKSUM
    assert service.workflow.rules_dir == (
        service.release_root / "campaign_optimizer/ontology/rules"
    )
    assert service.package_checksum == PINNED_CHECKSUM
    assert service.rule_statuses["R5"] == "PENDING_HUMAN_REVIEW"
    assert service.rule_statuses == {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["status"]
        for path in sorted(
            (service.release_root / "campaign_optimizer/ontology/rules").glob("R*.json")
        )
    }


def test_plan_review_service_fails_closed_when_bundle_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import app.services.plan_review_service as service_module

    missing_root = tmp_path / "missing-bundle"
    monkeypatch.setattr(service_module, "bundle_root", lambda manifest: missing_root)

    with pytest.raises(PackageDriftError):
        PlanReviewService(f"sqlite:///{tmp_path / 'missing.db'}", "demo_client_001")


def test_plan_review_service_fails_closed_when_bundle_is_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import app.services.plan_review_service as service_module

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "campaign_optimizer/ontology/publication_manifest.json"
        ).read_text(encoding="utf-8")
    )
    tampered_root = tmp_path / "tampered-bundle"
    first_asset = tampered_root / manifest["entries"][0]["path"]
    first_asset.parent.mkdir(parents=True)
    first_asset.write_bytes(b"tampered")
    monkeypatch.setattr(service_module, "bundle_root", lambda value: tampered_root)

    with pytest.raises(PackageDriftError):
        PlanReviewService(f"sqlite:///{tmp_path / 'tampered.db'}", "demo_client_001")

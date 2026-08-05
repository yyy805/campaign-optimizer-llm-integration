from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from sqlalchemy.orm import Session

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.db import ClientRow, init_db
from campaign_optimizer.ontology.publication import (
    load_publication_manifest,
    verify_publication_manifest,
)
from campaign_optimizer.ontology.review_workflow import ReviewRelease, ReviewWorkflow

from app.errors import AppError


class PlanReviewService:
    """Thin HTTP adapter over the sole canonical product workflow."""

    def __init__(self, database_url: str, client_id: str):
        self.project_root = Path(__file__).resolve().parents[3]
        self.manifest = load_publication_manifest(
            self.project_root / "campaign_optimizer/ontology/publication_manifest.json"
        )
        verify_publication_manifest(self.manifest, root=self.project_root)
        release = ReviewRelease.from_manifest(
            self.manifest,
            confidence_state_version="unprovisioned",
            root=self.project_root,
        )
        engine = init_db(database_url)
        with Session(engine) as session, session.begin():
            if session.get(ClientRow, client_id) is None:
                session.add(ClientRow(client_id=client_id, card={"client_id": client_id}))
        self.workflow = ReviewWorkflow(engine, release)
        self.client_id = client_id

    @property
    def package_checksum(self) -> str:
        return str(self.manifest["package_checksum"])

    @property
    def ontology_version(self) -> str:
        return str(self.manifest["ontology_version"])

    @property
    def concept_ids(self) -> list[str]:
        return sorted(path.stem for path in (
            self.project_root / "campaign_optimizer/ontology/concepts"
        ).glob("*.json"))

    @property
    def rule_statuses(self) -> dict[str, str]:
        rules = self.project_root / "campaign_optimizer/ontology/rules"
        return {
            path.stem: str(json.loads(path.read_text(encoding="utf-8"))["status"])
            for path in sorted(rules.glob("R*.json"))
        }

    @property
    def guardrail_ids(self) -> list[str]:
        return sorted(path.stem for path in (
            self.project_root / "campaign_optimizer/ontology/guardrails"
        ).glob("*.json"))

    @property
    def client_ids(self) -> list[str]:
        return sorted(path.stem for path in (
            self.project_root / "campaign_optimizer/ontology/clients"
        ).glob("*.json"))

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.workflow.review_final_plan(
                client_id=self.client_id, plan=payload, confidence_states={}
            )
        except ContractValidationError as exc:
            raise AppError(422, "INVALID_INPUT", str(exc)) from exc

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_ROOT = PROJECT_ROOT / "docs" / "ontology" / "ontology 概念卡"
API_KEY = "test-agent-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'review.db'}",
        ontology_path=ONTOLOGY_ROOT,
        api_key_principals=f"{API_KEY}:test-agent:tenant-a:SERVICE,test-reviewer-key:test-reviewer:tenant-a:REVIEWER",
        docs_enabled=True,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def review_payload(rule_ids: list[str], inputs: list[dict], *, client_id: str = "demo_client_001") -> dict:
    grain = "touchpoint" if any(rule_id in {"R3", "R5"} for rule_id in rule_ids) else "campaign"
    return {
        "client_id": client_id,
        "entity": {"grain": grain, "id": "test-entity"},
        "candidate_rules": rule_ids,
        "inputs": inputs,
        "expected_ontology_version": "v1.1-demo",
    }

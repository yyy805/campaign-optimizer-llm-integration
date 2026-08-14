from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from app.db import Database


API_ROOT = Path(__file__).resolve().parents[1]


def test_compose_preserves_database_url_from_env_file():
    compose = yaml.safe_load((API_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["review-api"]
    assert service["env_file"] == [".env"]
    assert "DATABASE_URL" not in service.get("environment", {})


def test_runtime_installs_psycopg_and_documents_safe_defaults():
    project = (API_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    example = (API_ROOT / ".env.example").read_text(encoding="utf-8")
    assert '"psycopg[binary]==3.3.4"' in project
    assert "DATABASE_URL=sqlite:////data/review.db" in example
    assert "postgresql+psycopg://" in example
    assert "ENCODED_PASSWORD" in example


def test_encoded_postgres_password_survives_alembic_config():
    url = "postgresql+psycopg://review_api:p%40ss%25word@db.invalid:5432/review_test?connect_timeout=5"
    database = Database(url)
    try:
        assert database._alembic_config().get_main_option("sqlalchemy.url") == url
    finally:
        database.close()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:password@db.invalid/review_test?connect_timeout=5",
        "postgresql+psycopg://user:password@db.invalid/review_test",
        "postgresql+psycopg://user:password@db.invalid/review_test?connect_timeout=0",
        "postgresql+psycopg://user:password@db.invalid/review_test?connect_timeout=forever",
    ],
)
def test_invalid_postgres_driver_or_timeout_fails_before_connecting(url: str):
    with pytest.raises(ValueError):
        Database(url)

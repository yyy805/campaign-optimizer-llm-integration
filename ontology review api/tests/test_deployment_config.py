from __future__ import annotations

import ast
from pathlib import Path

import yaml
import pytest
from sqlalchemy import inspect, text

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


def test_runtime_image_includes_frozen_ontology_bundles():
    dockerfile = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'COPY [".ontology_bundles", "./.ontology_bundles"]' in dockerfile


def test_encoded_postgres_password_survives_alembic_config():
    url = "postgresql+psycopg://review_api:p%40ss%25word@db.invalid:5432/review_test?connect_timeout=5"
    database = Database(url)
    try:
        assert database._alembic_config().get_main_option("sqlalchemy.url") == url
    finally:
        database.close()


def test_migration_environment_uses_dedicated_api_version_table():
    source = (API_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    configure_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "context"
        and node.func.attr == "configure"
    ]

    assert len(configure_calls) == 2
    for call in configure_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        value = keywords["version_table"]
        assert isinstance(value, ast.Constant)
        assert value.value == Database.ALEMBIC_VERSION_TABLE


def test_api_migration_ignores_unrelated_root_alembic_ledger(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'shared.db'}")
    try:
        with database.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": "7b8f3d1a2c4e"},
            )

        database.migrate()

        with database.engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            root_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            api_revision = connection.execute(
                text("SELECT version_num FROM api_alembic_version")
            ).scalar_one()

        assert {"alembic_version", Database.ALEMBIC_VERSION_TABLE}.issubset(tables)
        assert root_revision == "7b8f3d1a2c4e"
        assert api_revision == "0003_plan_review_hardening"
        assert database.check() is True
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

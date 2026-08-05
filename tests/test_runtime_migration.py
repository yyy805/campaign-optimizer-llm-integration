from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
import pytest

from campaign_optimizer.ontology.db import (
    Base, ClientRow, ConceptRow, DiagnosisRow, ExecutionLogRow, RuleRow,
)


LEGACY_TABLES = [
    ConceptRow.__table__, RuleRow.__table__, ClientRow.__table__,
    DiagnosisRow.__table__, ExecutionLogRow.__table__,
]
RUNTIME_TABLES = {
    'model_artifacts', 'plan_snapshots', 'plan_items', 'ontology_reviews',
    'ontology_review_items', 'feedback_events', 'rule_confidence_states',
    'plan_decision_events',
}


def test_alembic_upgrades_legacy_five_table_database(tmp_path):
    database = tmp_path / 'migration.db'
    url = 'sqlite:///' + str(database)
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=LEGACY_TABLES)

    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, 'head')

    tables = set(inspect(engine).get_table_names())
    assert {table.name for table in LEGACY_TABLES}.issubset(tables)
    assert RUNTIME_TABLES.issubset(tables)
    assert 'alembic_version' in tables
    inspector = inspect(engine)
    for table_name in RUNTIME_TABLES:
        migrated = {column['name'] for column in inspector.get_columns(table_name)}
        modeled = {column.name for column in Base.metadata.tables[table_name].columns}
        assert migrated == modeled, table_name
    unique_names = {
        item['name'] for item in inspector.get_unique_constraints('ontology_reviews')
    }
    assert {'uq_review_plan', 'uq_review_revision'}.issubset(unique_names)
    foreign_keys = {
        tuple(item['constrained_columns'])
        for item in inspector.get_foreign_keys('ontology_reviews')
    }
    assert ('client_id', 'parent_review_id') in foreign_keys


def test_downgrade_fails_closed_without_partially_dropping_runtime_tables(tmp_path):
    database = tmp_path / 'downgrade.db'
    url = 'sqlite:///' + str(database)
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=LEGACY_TABLES)
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, 'head')
    before = set(inspect(engine).get_table_names())

    with pytest.raises(RuntimeError, match='forward-only'):
        command.downgrade(config, 'base')

    after = set(inspect(engine).get_table_names())
    assert after == before
    assert RUNTIME_TABLES.issubset(after)
    assert 'alembic_version' in after

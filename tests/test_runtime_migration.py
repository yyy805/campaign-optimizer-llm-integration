from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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

"""
pytest 测试：S0.2 验收——建库脚本。

覆盖：
  - SQLite 连接串下跑通一次：五张表全部建出来；
  - 幂等：drop_first=True 时重跑不报错、表结构不变；
  - 能把一张概念卡的 JSON 内容原样写入 concepts 表并读出来（证明 JSON 列不丢字段）。

PostgreSQL 连接串暂不在本地跑集成测试（本机没有可用的 PG 实例），
但 init_db() 对 SQLite/PostgreSQL 走的是同一段代码、同一套 SQLAlchemy 模型，
不含任何方言专属语法（详见 db.py 里放弃 JSONB、改用通用 JSON 类型的说明），
真正的 PG 连接验证留到部署阶段用真实 RDS/PolarDB 连接串跑一次（计划 S0.2 验收原文）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.db import (
    ClientRow, ConceptRow, PlanItemRow, PlanSnapshotRow, RuleConfidenceStateRow,
    canonical_digest, init_db,
)

ONTOLOGY_DIR = Path(__file__).parent.parent / "campaign_optimizer" / "ontology"

EXPECTED_TABLES = {"concepts", "rules", "clients", "diagnoses", "execution_log"}


EXPECTED_TABLES = EXPECTED_TABLES | {
    'model_artifacts', 'plan_snapshots', 'plan_items', 'ontology_reviews',
    'ontology_review_items', 'feedback_events', 'rule_confidence_states',
    'plan_decision_events',
}


def test_init_db_creates_static_and_runtime_tables_on_sqlite(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = init_db(db_url)
    with engine.connect() as conn:
        table_names = set(inspect(conn).get_table_names())
    assert table_names == EXPECTED_TABLES


def test_init_db_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    engine = init_db(db_url)  # 重跑不报错
    with engine.connect() as conn:
        table_names = set(inspect(conn).get_table_names())
    assert table_names == EXPECTED_TABLES


def test_init_db_default_keeps_existing_data(tmp_path):
    """Murat 审查意见：默认行为不该清空数据，这里实际验证一下，不只是靠注释承诺。"""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = init_db(db_url)
    with Session(engine) as session:
        session.add(ConceptRow(concept_id="roas", layer="L5", tier="derived", caliber="platform", card={}))
        session.commit()

    init_db(db_url)  # 默认不清空，再跑一次

    with Session(engine) as session:
        assert session.execute(select(ConceptRow)).scalars().all()


def test_init_db_reset_wipes_existing_data(tmp_path):
    """drop_first=True（CLI 里对应 --reset）必须真的清空数据，不能只是个摆设选项。"""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = init_db(db_url)
    with Session(engine) as session:
        session.add(ConceptRow(concept_id="roas", layer="L5", tier="derived", caliber="platform", card={}))
        session.commit()

    init_db(db_url, drop_first=True)  # 显式重置

    with Session(engine) as session:
        assert session.execute(select(ConceptRow)).scalars().all() == []


def test_cross_client_plan_item_reference_is_rejected(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'tenant.db'))
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add_all([ClientRow(client_id='c1', card={}), ClientRow(client_id='c2', card={})])
        session.flush()
        session.add(PlanSnapshotRow(client_id='c1', plan_id='plan_1', source_artifact_id=None,
            source_version='1', plan_digest=canonical_digest({}), created_at=now, payload={}))
        session.commit()
        session.add(PlanItemRow(client_id='c2', plan_id='plan_1', plan_item_id='item_1',
            entity_id='x', action='keep_budget', payload={}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_plan_digest_must_match_payload(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'digest.db'))
    with Session(engine) as session:
        session.add(ClientRow(client_id='c1', card={}))
        session.flush()
        session.add(PlanSnapshotRow(client_id='c1', plan_id='plan_1', source_artifact_id=None,
            source_version='1', plan_digest='a' * 64,
            created_at=datetime.now(timezone.utc), payload={'changed': True}))
        with pytest.raises(ContractValidationError, match='digest'):
            session.commit()


def test_confidence_outside_zero_to_one_is_rejected(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'confidence.db'))
    payload = {
        'runtime_confidence': 1.2, 'status': 'ACTIVE',
    }
    with Session(engine) as session:
        session.add(ClientRow(client_id='c1', card={}))
        session.flush()
        session.add(RuleConfidenceStateRow(client_id='c1', rule_id='R1', rule_version='1',
            runtime_confidence=1.2, status='ACTIVE', revision=0,
            updated_at=datetime.now(timezone.utc), payload=payload))
        with pytest.raises(IntegrityError):
            session.commit()


def test_concept_card_round_trips_through_json_column(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = init_db(db_url)

    roas_card = json.loads((ONTOLOGY_DIR / "concepts" / "roas.json").read_text(encoding="utf-8"))

    with Session(engine) as session:
        session.add(
            ConceptRow(
                concept_id=roas_card["concept_id"],
                layer=roas_card["layer"],
                tier=roas_card["tier"],
                caliber=roas_card["caliber"],
                card=roas_card,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.execute(
            select(ConceptRow).where(ConceptRow.concept_id == "roas")
        ).scalar_one()
        assert row.card == roas_card
        assert row.card["granularity"]["aggregation"] == "recompute"

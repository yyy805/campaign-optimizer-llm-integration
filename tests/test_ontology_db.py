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
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from campaign_optimizer.ontology.db import ConceptRow, init_db

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

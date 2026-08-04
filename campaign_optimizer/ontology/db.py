"""
本体运行时数据库层：concepts / rules / clients / diagnoses / execution_log 五张表。

设计原则（S0.2）：
  - 每张表主体是一个 JSON 列，存整张卡的完整内容，字段以 schemas/ 下的 JSON Schema 为准；
  - 少量索引列供快速查询，不重复 JSON 里已有的信息；
  - 一套模型定义，只换连接串即可在 SQLite / PostgreSQL 间迁移：SQLite 上是通用 JSON
    （文本存储），PostgreSQL/PolarDB 上通过 with_variant 自动换成原生 JSONB（二进制
    归一化、可建 GIN 索引），业务代码不用感知这个差异（Murat 审查后订正）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKeyConstraint, Integer, String, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from campaign_optimizer.contracts.feedback import apply_feedback_event
from campaign_optimizer.contracts.validation import ContractValidationError

# SQLite 上仍是通用 JSON（文本存储）；PostgreSQL/PolarDB 上自动变成真正的 JSONB
# （二进制归一化、可建 GIN 索引），不用改一行业务代码，代价为零（Murat 审查意见）。
JSONColumn = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class ConceptRow(Base):
    """一张概念卡 = 一行；card 是卡片的完整 JSON 内容。"""

    __tablename__ = "concepts"

    concept_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    layer: Mapped[str] = mapped_column(String(8), index=True)
    tier: Mapped[str] = mapped_column(String(16), index=True)
    caliber: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    card: Mapped[dict] = mapped_column(JSONColumn)


class RuleRow(Base):
    """一条规则卡 = 一行；card 是卡片的完整 JSON 内容。"""

    __tablename__ = "rules"

    rule_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    risk_level: Mapped[str] = mapped_column(String(16), index=True)
    attribution_model: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    card: Mapped[dict] = mapped_column(JSONColumn)


class ClientRow(Base):
    """一个客户档案 = 一行（ACoS 目标、风险容忍度、审批门槛、版本锁定等）。"""

    __tablename__ = "clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card: Mapped[dict] = mapped_column(JSONColumn)


class DiagnosisRow(Base):
    """推理引擎跑出的一条诊断结果。"""

    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[str] = mapped_column(String(16), index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class ExecutionLogRow(Base):
    """一次建议采纳/执行/回滚的留痕记录。"""

    __tablename__ = "execution_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class ModelArtifactRow(Base):
    __tablename__ = 'model_artifacts'
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), index=True)
    content_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanSnapshotRow(Base):
    __tablename__ = 'plan_snapshots'
    __table_args__ = (ForeignKeyConstraint(
        ['client_id', 'source_artifact_id'],
        ['model_artifacts.client_id', 'model_artifacts.artifact_id'],
    ),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_version: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanItemRow(Base):
    __tablename__ = 'plan_items'
    __table_args__ = (ForeignKeyConstraint(
        ['client_id', 'plan_id'],
        ['plan_snapshots.client_id', 'plan_snapshots.plan_id'],
    ),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class OntologyReviewRow(Base):
    __tablename__ = 'ontology_reviews'
    __table_args__ = (ForeignKeyConstraint(['client_id', 'plan_id'], ['plan_snapshots.client_id', 'plan_snapshots.plan_id']),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    ontology_version: Mapped[str] = mapped_column(String(64))
    overall_verdict: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


class OntologyReviewItemRow(Base):
    __tablename__ = 'ontology_review_items'
    __table_args__ = (ForeignKeyConstraint(['client_id', 'review_id'], ['ontology_reviews.client_id', 'ontology_reviews.review_id']),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_item_id: Mapped[str] = mapped_column(String(128))
    rule_id: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(32))
    confidence_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class FeedbackEventRow(Base):
    __tablename__ = 'feedback_events'
    __table_args__ = (ForeignKeyConstraint(
        ['client_id', 'review_id', 'review_item_id'],
        ['ontology_review_items.client_id', 'ontology_review_items.review_id', 'ontology_review_items.review_item_id'],
    ),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128))
    review_item_id: Mapped[str] = mapped_column(String(128))
    rule_id: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[str] = mapped_column(String(32))
    rating: Mapped[str] = mapped_column(String(16))
    event_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


class RuleConfidenceStateRow(Base):
    __tablename__ = 'rule_confidence_states'
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rule_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    runtime_confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanDecisionEventRow(Base):
    __tablename__ = 'plan_decision_events'
    __table_args__ = (ForeignKeyConstraint(['client_id', 'plan_id'], ['plan_snapshots.client_id', 'plan_snapshots.plan_id']),)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONColumn)


def build_engine(db_url: str) -> Engine:
    engine = create_engine(db_url)
    if engine.dialect.name == 'sqlite':
        event.listen(engine, 'connect', _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.close()


def canonical_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def apply_feedback_transaction(
    engine: Engine, *, client_id: str, event_payload: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    '''Write the immutable feedback and confidence update in one transaction.'''
    digest = canonical_digest(event_payload)
    with Session(engine) as session, session.begin():
        existing = session.get(FeedbackEventRow, (client_id, event_payload['feedback_id']))
        key = (client_id, event_payload['rule_id'], event_payload['rule_version'])
        state = session.get(RuleConfidenceStateRow, key)
        if existing is not None:
            if existing.event_digest != digest:
                raise ContractValidationError('duplicate feedback_id has a different payload')
            if state is None:
                raise ContractValidationError('confidence state does not exist')
            return dict(state.payload)
        review = session.get(OntologyReviewRow, (client_id, event_payload['review_id']))
        if review is None or state is None:
            raise ContractValidationError('review or confidence state does not exist')
        updated = apply_feedback_event(dict(state.payload), event_payload, dict(review.payload), policy)
        created_at = datetime.fromisoformat(event_payload['created_at'].replace('Z', '+00:00'))
        session.add(FeedbackEventRow(
            client_id=client_id, feedback_id=event_payload['feedback_id'],
            review_id=event_payload['review_id'], review_item_id=event_payload['review_item_id'],
            rule_id=event_payload['rule_id'], rule_version=event_payload['rule_version'],
            rating=event_payload['rating'], event_digest=digest, created_at=created_at,
            payload=event_payload,
        ))
        state.runtime_confidence = updated['runtime_confidence']
        state.status = updated['status']
        state.revision += 1
        state.updated_at = created_at
        state.payload = updated
        return dict(updated)


def init_db(db_url: str, *, drop_first: bool = False) -> Engine:
    """建库：默认只补建缺失的表，不动已有数据。

    drop_first=True 时先清空重建（S0.2 要求的"重跑=重建"、设计清单 #42"一键重置"
    的底座）——但这是破坏性操作，默认关闭，调用方必须显式传 True 才会清空
    （Murat 审查意见：清空重建不该是无提示默认值，一旦库里有真实概念卡/规则卡数据，
    误跑会静默丢数据）。
    """
    engine = build_engine(db_url)
    if drop_first:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine

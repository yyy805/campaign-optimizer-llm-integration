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
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON, CheckConstraint, Date, DateTime, Float, ForeignKey,
    ForeignKeyConstraint, Integer, String, UniqueConstraint, create_engine, event, select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator

from campaign_optimizer.contracts.feedback import apply_feedback_event
from campaign_optimizer.contracts.validation import ContractValidationError, validate_contract_object

# SQLite 上仍是通用 JSON（文本存储）；PostgreSQL/PolarDB 上自动变成真正的 JSONB
# （二进制归一化、可建 GIN 索引），不用改一行业务代码，代价为零（Murat 审查意见）。
JSONColumn = JSON().with_variant(JSONB, "postgresql")


class UTCDateTime(TypeDecorator):
    '''Store aware UTC datetimes and restore SQLite values with UTC tzinfo.'''

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError('runtime timestamps must be timezone-aware')
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


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
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'parent_artifact_id'], ['model_artifacts.client_id', 'model_artifacts.artifact_id']),
        CheckConstraint('length(content_digest) = 64', name='ck_artifact_digest_length'),
        CheckConstraint('period_end IS NULL OR period_start IS NULL OR period_end >= period_start', name='ck_artifact_period'),
    )
    client_id: Mapped[str] = mapped_column(ForeignKey('clients.client_id'), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    parent_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanSnapshotRow(Base):
    __tablename__ = 'plan_snapshots'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'source_artifact_id'], ['model_artifacts.client_id', 'model_artifacts.artifact_id']),
        CheckConstraint('length(plan_digest) = 64', name='ck_plan_digest_length'),
    )
    client_id: Mapped[str] = mapped_column(ForeignKey('clients.client_id'), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_version: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanItemRow(Base):
    __tablename__ = 'plan_items'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'plan_id'], ['plan_snapshots.client_id', 'plan_snapshots.plan_id']),
        CheckConstraint('action IS NULL OR action IN (\'increase_budget\',\'decrease_budget\',\'keep_budget\')', name='ck_plan_item_action'),
    )
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class OntologyReviewRow(Base):
    __tablename__ = 'ontology_reviews'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'plan_id'], ['plan_snapshots.client_id', 'plan_snapshots.plan_id']),
        ForeignKeyConstraint(
            ['client_id', 'parent_review_id'],
            ['ontology_reviews.client_id', 'ontology_reviews.review_id'],
            name='fk_review_parent',
        ),
        UniqueConstraint('client_id', 'review_id', 'plan_id', name='uq_review_plan'),
        UniqueConstraint('client_id', 'plan_id', 'revision', name='uq_review_revision'),
        CheckConstraint('revision >= 0', name='ck_review_revision'),
        CheckConstraint('overall_verdict IN (\'SUPPORT\',\'CONFLICT\',\'NOT_APPLICABLE\',\'UNVERIFIED\',\'INSUFFICIENT_EVIDENCE\')', name='ck_review_verdict'),
    )
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    parent_review_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    ontology_version: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64), default='legacy')
    engine_version: Mapped[str] = mapped_column(String(64), default='legacy')
    schema_version: Mapped[str] = mapped_column(String(64), default='legacy')
    source_commit: Mapped[str] = mapped_column(String(40), default='0' * 40)
    package_checksum: Mapped[str] = mapped_column(String(64), default='0' * 64)
    confidence_state_version: Mapped[str] = mapped_column(String(128), default='legacy')
    overall_verdict: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict] = mapped_column(JSONColumn)


class OntologyReviewItemRow(Base):
    __tablename__ = 'ontology_review_items'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'review_id', 'plan_id'], ['ontology_reviews.client_id', 'ontology_reviews.review_id', 'ontology_reviews.plan_id']),
        ForeignKeyConstraint(['client_id', 'plan_id', 'plan_item_id'], ['plan_items.client_id', 'plan_items.plan_id', 'plan_items.plan_item_id']),
        CheckConstraint('verdict IN (\'SUPPORT\',\'CONFLICT\',\'NOT_APPLICABLE\',\'UNVERIFIED\',\'INSUFFICIENT_EVIDENCE\')', name='ck_review_item_verdict'),
        CheckConstraint('confidence_snapshot IS NULL OR (confidence_snapshot >= 0 AND confidence_snapshot <= 1)', name='ck_review_item_confidence'),
    )
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    plan_item_id: Mapped[str] = mapped_column(String(128))
    rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verdict: Mapped[str] = mapped_column(String(32))
    confidence_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class FeedbackEventRow(Base):
    __tablename__ = 'feedback_events'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'review_id', 'review_item_id'], ['ontology_review_items.client_id', 'ontology_review_items.review_id', 'ontology_review_items.review_item_id']),
        CheckConstraint('rating IN (\'GOOD\',\'FINE\',\'BAD\')', name='ck_feedback_rating'),
        CheckConstraint('length(event_digest) = 64', name='ck_feedback_digest_length'),
    )
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(128))
    review_item_id: Mapped[str] = mapped_column(String(128))
    rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rating: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    event_digest: Mapped[str] = mapped_column(String(64))
    event_created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn)


class RuleConfidenceStateRow(Base):
    __tablename__ = 'rule_confidence_states'
    __table_args__ = (
        CheckConstraint('runtime_confidence >= 0 AND runtime_confidence <= 1', name='ck_state_confidence'),
        CheckConstraint('revision >= 0', name='ck_state_revision'),
        CheckConstraint('status IN (\'ACTIVE\',\'PENDING_HUMAN_REVIEW\',\'SUSPENDED\',\'RETIRED\')', name='ck_state_status'),
    )
    client_id: Mapped[str] = mapped_column(ForeignKey('clients.client_id'), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rule_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    runtime_confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict] = mapped_column(JSONColumn)


class PlanDecisionEventRow(Base):
    __tablename__ = 'plan_decision_events'
    __table_args__ = (
        ForeignKeyConstraint(['client_id', 'plan_id'], ['plan_snapshots.client_id', 'plan_snapshots.plan_id']),
        CheckConstraint('decision IN (\'ACCEPT\',\'REJECT\')', name='ck_plan_decision'),
    )
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
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


IMMUTABLE_RUNTIME_ROWS = (
    ModelArtifactRow, PlanSnapshotRow, PlanItemRow, OntologyReviewRow,
    OntologyReviewItemRow, FeedbackEventRow, PlanDecisionEventRow,
)


@event.listens_for(Session, 'before_flush')
def _validate_runtime_rows(session: Session, _flush_context: Any, _instances: Any) -> None:
    for row in session.dirty.union(session.deleted):
        if isinstance(row, IMMUTABLE_RUNTIME_ROWS):
            raise ContractValidationError('runtime snapshots and events are immutable')
    for row in session.new.union(session.dirty):
        if isinstance(row, ModelArtifactRow) and row.content_digest != canonical_digest(row.payload):
            raise ContractValidationError('model artifact digest does not match payload')
        if isinstance(row, PlanSnapshotRow) and row.plan_digest != canonical_digest(row.payload):
            raise ContractValidationError('plan digest does not match payload')
        if isinstance(row, FeedbackEventRow):
            if row.event_digest != canonical_digest(row.payload):
                raise ContractValidationError('feedback digest does not match payload')
            projection = {
                'feedback_id': row.feedback_id, 'review_id': row.review_id,
                'review_item_id': row.review_item_id, 'rule_id': row.rule_id,
                'rule_version': row.rule_version, 'rating': row.rating,
                'actor_id': row.actor_id,
            }
            if any(row.payload.get(key) != value for key, value in projection.items()):
                raise ContractValidationError('feedback relational projection does not match payload')
        if isinstance(row, OntologyReviewRow):
            projection = {'review_id': row.review_id, 'plan_id': row.plan_id,
                'ontology_version': row.ontology_version, 'overall_verdict': row.overall_verdict}
            if any(row.payload.get(key) != value for key, value in projection.items()):
                raise ContractValidationError('review relational projection does not match payload')
        if isinstance(row, OntologyReviewItemRow):
            projection = {'review_item_id': row.review_item_id, 'plan_item_id': row.plan_item_id,
                'rule_id': row.rule_id, 'rule_version': row.rule_version, 'verdict': row.verdict}
            if any(row.payload.get(key) != value for key, value in projection.items()):
                raise ContractValidationError('review item projection does not match payload')
        if isinstance(row, RuleConfidenceStateRow):
            expected = (row.payload.get('runtime_confidence'), row.payload.get('status'))
            if expected != (row.runtime_confidence, row.status):
                raise ContractValidationError('confidence relational projection does not match payload')


def _feedback_result(
    status: str, feedback_id: str, applied_revision: int | None,
    state: RuleConfidenceStateRow | None,
) -> dict[str, Any]:
    return {
        'application_status': status,
        'feedback_id': feedback_id,
        'applied_revision': applied_revision,
        'current_revision': state.revision if state is not None else None,
        'state': dict(state.payload) if state is not None else None,
    }


def apply_feedback_transaction(
    engine: Engine, *, client_id: str, event_payload: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    '''Apply feedback atomically; serialize SQLite writers and lock PG state rows.'''
    digest = canonical_digest(event_payload)
    connection = engine.connect()
    if engine.dialect.name == 'sqlite':
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        session = Session(bind=connection, join_transaction_mode='control_fully')
    else:
        session = Session(bind=connection)
        session.begin()
    try:
        state = None
        if event_payload['rule_id'] is not None:
            statement = select(RuleConfidenceStateRow).where(
                RuleConfidenceStateRow.client_id == client_id,
                RuleConfidenceStateRow.rule_id == event_payload['rule_id'],
                RuleConfidenceStateRow.rule_version == event_payload['rule_version'],
            ).with_for_update()
            state = session.execute(statement).scalar_one_or_none()
        existing = session.get(FeedbackEventRow, (client_id, event_payload['feedback_id']))
        if existing is not None:
            if existing.event_digest != digest:
                raise ContractValidationError('duplicate feedback_id has a different payload')
            session.commit()
            return _feedback_result('ALREADY_APPLIED', existing.feedback_id, existing.applied_revision, state)
        review = session.get(OntologyReviewRow, (client_id, event_payload['review_id']))
        if review is None:
            raise ContractValidationError('ontology review does not exist')
        validate_contract_object('feedback_event', event_payload)
        validate_contract_object('ontology_review', review.payload)
        review_item = next((item for item in review.payload['items'] if item['review_item_id'] == event_payload['review_item_id']), None)
        if review_item is None or event_payload['plan_id'] != review.payload['plan_id']:
            raise ContractValidationError('feedback does not match stored review')
        expected = {key: review_item[key] for key in ('plan_item_id', 'rule_id', 'rule_version', 'verdict')}
        if expected != {key: event_payload[key] for key in expected}:
            raise ContractValidationError('feedback payload does not match reviewed item snapshot')

        received_at = datetime.now(timezone.utc)
        event_created_at = datetime.fromisoformat(event_payload['created_at'].replace('Z', '+00:00'))
        applied_revision = None
        if state is not None:
            updated = apply_feedback_event(dict(state.payload), event_payload, dict(review.payload), policy)
            updated['updated_at'] = received_at.isoformat().replace('+00:00', 'Z')
            validate_contract_object('confidence_state', updated)
            state.runtime_confidence = updated['runtime_confidence']
            state.status = updated['status']
            state.revision += 1
            state.updated_at = received_at
            state.payload = updated
            applied_revision = state.revision
        elif event_payload['rule_id'] is not None:
            raise ContractValidationError('confidence state does not exist')
        session.add(FeedbackEventRow(
            client_id=client_id, feedback_id=event_payload['feedback_id'],
            review_id=event_payload['review_id'], review_item_id=event_payload['review_item_id'],
            rule_id=event_payload['rule_id'], rule_version=event_payload['rule_version'],
            rating=event_payload['rating'], actor_id=event_payload['actor_id'],
            event_digest=digest, event_created_at=event_created_at, received_at=received_at,
            applied_revision=applied_revision, payload=event_payload,
        ))
        session.commit()
        return _feedback_result('APPLIED', event_payload['feedback_id'], applied_revision, state)
    except IntegrityError:
        session.rollback()
        with Session(engine) as replay_session:
            existing = replay_session.get(FeedbackEventRow, (client_id, event_payload['feedback_id']))
            if existing is None or existing.event_digest != digest:
                raise
            state = None
            if event_payload['rule_id'] is not None:
                state = replay_session.get(RuleConfidenceStateRow, (client_id, event_payload['rule_id'], event_payload['rule_version']))
            return _feedback_result('ALREADY_APPLIED', existing.feedback_id, existing.applied_revision, state)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        connection.close()


def init_db(db_url: str, *, drop_first: bool = False) -> Engine:
    """建库：默认只补建缺失的表，不动已有数据。

    drop_first=True 时先清空重建（S0.2 要求的"重跑=重建"、设计清单 #42"一键重置"
    的底座）——但这是破坏性操作，默认关闭，调用方必须显式传 True 才会清空
    （Murat 审查意见：清空重建不该是无提示默认值，一旦库里有真实概念卡/规则卡数据，
    误跑会静默丢数据）。
    """
    engine = build_engine(db_url)
    if drop_first:
        if engine.dialect.name != 'sqlite':
            raise ValueError('drop_first is restricted to local SQLite databases')
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine

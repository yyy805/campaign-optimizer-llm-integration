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

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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


def build_engine(db_url: str) -> Engine:
    return create_engine(db_url)


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

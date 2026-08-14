#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQLite → PostgreSQL 换库演练:小数据量通用搬迁脚本。

用法:
    uv run --with psycopg2-binary python scripts/drill_migrate_sqlite_to_pg.py --sqlite <path>

离线自检(不连 PG,只内省源库结构并打印表/列/行数报告):
    uv run python scripts/drill_migrate_sqlite_to_pg.py --sqlite <path> --check-source

前提与约定:
    - 连接串只从环境变量 PG_DRILL_URL 读取(形如 postgresql://user:***@host:5432/dbname,
      可带 ?sslmode=require)。凭据绝不写入本仓库任何文件、不进聊天与截图。
    - 目标表必须已由 alembic 建好(见 docs/pg-drill-runbook.md 第 3 步);
      本脚本只搬数据、不建表、不改 schema。
    - 只复制"源 SQLite 与目标 PG 都存在"的表,按列名交集 INSERT;
      PG 里多出的列走数据库默认值,SQLite 里多出的列会被明确报出并停止(不静默丢列)。
    - 按 PRAGMA foreign_key_list 做表级拓扑排序插入;自引用表
      (model_artifacts.parent_artifact_id、ontology_reviews.parent_review_id)做行级排序。
    - 类型问题(JSON 解析失败、时间戳/数值非法等)立即中止,报错含 表名/列名/行上下文。

跑之前必须先停写(停掉一切会写源 SQLite 的服务),否则搬迁期间新增的行会丢失或分叉。
脚本会打印停写确认,必须显式加 --yes 才真正执行。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

IGNORED_PREFIXES = ("sqlite_",)

# SQLAlchemy 在 SQLite 上把 JSON 列存成 TEXT、把 DateTime(timezone=True) 存成 ISO 文本;
# 搬到 PG 时按目标列的 information_schema.data_type 做显式转换。
JSON_TYPES = {"json", "jsonb"}
TIMESTAMP_TYPES = {"timestamp with time zone", "timestamp without time zone"}
DATE_TYPES = {"date"}
INT_TYPES = {"smallint", "integer", "bigint"}
FLOAT_TYPES = {"real", "double precision", "numeric"}
BOOL_TYPES = {"boolean"}


def fail(message: str):
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def normalize_pg_dsn(url: str) -> str:
    """把 SQLAlchemy 风格的连接串归一成 psycopg2 可直接 connect 的形式。"""
    url = url.strip().strip("'\"")
    if not url:
        fail("PG_DRILL_URL 为空:请在环境变量里提供 PG 连接串(不要写进任何文件)。")
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres://"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgresql://"):
        return url
    if url.startswith("postgresql+psycopg://"):
        fail(
            "PG_DRILL_URL 用了 postgresql+psycopg://(psycopg3)方案;"
            "本脚本用 psycopg2,请改成 postgresql:// 或 postgresql+psycopg2://。"
        )
    fail(f"PG_DRILL_URL 方案无法识别(应为 postgresql://...):{url.split('://', 1)[0]}")


def introspect_sqlite(sqlite_path: Path) -> dict:
    """PRAGMA 内省:表、列、主键、外键、行数。返回按 FK 依赖拓扑排序的表清单。"""
    if not sqlite_path.exists():
        fail(f"源 SQLite 文件不存在:{sqlite_path}")
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            # alembic_version 是迁移台账(目标库自有),不是业务数据,不搬
            if not row["name"].startswith(IGNORED_PREFIXES)
            and row["name"] != "alembic_version"
        ]
        info: dict[str, dict] = {}
        deps: dict[str, set[str]] = {}
        for table in tables:
            columns = [
                {
                    "name": row["name"],
                    "declared_type": row["type"],
                    # pk 是 1 起始的序号(0=非主键),复合主键按此排序
                    "pk": int(row["pk"]),
                }
                for row in conn.execute(f"PRAGMA table_info('{table}')")
            ]
            # PRAGMA foreign_key_list 每个(外键, 列对)一行;按外键 id 归组,
            # 复合外键(如 ontology_reviews 的 (client_id, parent_review_id))保持 seq 顺序。
            grouped: dict[int, dict] = {}
            for row in conn.execute(f"PRAGMA foreign_key_list('{table}')"):
                entry = grouped.setdefault(row["id"], {"table": row["table"], "pairs": []})
                entry["pairs"].append({"seq": row["seq"], "from": row["from"], "to": row["to"]})
            fks = [grouped[key] for key in sorted(grouped)]
            row_count = conn.execute(f"SELECT COUNT(*) AS n FROM \"{table}\"").fetchone()["n"]
            info[table] = {"columns": columns, "fks": fks, "row_count": row_count}
            deps[table] = {fk["table"] for fk in fks if fk["table"] != table and fk["table"] in tables}
        ordered = topological_sort(tables, deps)
        return {"conn": conn, "tables": info, "order": ordered}
    except Exception:
        conn.close()
        raise


def topological_sort(tables: list[str], deps: dict[str, set[str]]) -> list[str]:
    remaining = set(tables)
    ordered: list[str] = []
    while remaining:
        ready = sorted(t for t in remaining if not (deps[t] & remaining))
        if not ready:
            fail(f"表间外键存在环,无法确定插入顺序:{sorted(remaining)}")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def print_source_report(introspection: dict, sqlite_path: Path) -> None:
    print(f"源库内省报告:{sqlite_path}")
    print(f"{'table':32} {'rows':>8}  columns")
    for table in introspection["order"]:
        meta = introspection["tables"][table]
        columns = ", ".join(column["name"] for column in meta["columns"])
        print(f"{table:32} {meta['row_count']:>8}  {columns}")


def parse_timestamp(value, table: str, column: str):
    if isinstance(value, (datetime, date)):
        return value
    if not isinstance(value, str) or not value.strip():
        fail(f"{table}.{column}:期望 ISO 时间戳文本,实际得到 {value!r}")
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        fail(f"{table}.{column}:无法解析为时间戳:{value!r}(SQLite 侧应为 SQLAlchemy 写入的 ISO 文本)")


def parse_date(value, table: str, column: str):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        fail(f"{table}.{column}:期望 ISO 日期文本,实际得到 {value!r}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        fail(f"{table}.{column}:无法解析为日期:{value!r}")


def parse_json(value, table: str, column: str):
    if value is None:
        fail(f"{table}.{column}:JSON 列不允许 NULL(schema 声明 NOT NULL)。")
    if not isinstance(value, str):
        fail(f"{table}.{column}:SQLite 侧 JSON 应为 TEXT,实际得到 {type(value).__name__}")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        fail(f"{table}.{column}:JSON 解析失败:{exc};片段:{value[:120]!r}")


def convert_value(value, pg_type: str, table: str, column: str):
    if value is None:
        if pg_type in JSON_TYPES:
            parse_json(None, table, column)  # JSON 列在 schema 中 NOT NULL,此处明确报错
        return ("raw", None)
    if pg_type in JSON_TYPES:
        return ("json", parse_json(value, table, column))
    if pg_type in TIMESTAMP_TYPES:
        return ("raw", parse_timestamp(value, table, column))
    if pg_type in DATE_TYPES:
        return ("raw", parse_date(value, table, column))
    if pg_type in INT_TYPES:
        if isinstance(value, int):
            return ("raw", value)
        try:
            return ("raw", int(str(value).strip()))
        except ValueError:
            fail(f"{table}.{column}:PG 目标为 {pg_type},但 SQLite 值不是整数:{value!r}")
    if pg_type in FLOAT_TYPES:
        if isinstance(value, (int, float)):
            return ("raw", float(value))
        try:
            return ("raw", float(str(value).strip()))
        except ValueError:
            fail(f"{table}.{column}:PG 目标为 {pg_type},但 SQLite 值不是数值:{value!r}")
    if pg_type in BOOL_TYPES:
        return ("raw", bool(value))
    return ("raw", value)


def order_rows_for_self_fk(rows: list[dict], pk_columns: list[str], self_fks: list[dict], table: str) -> list[dict]:
    """自引用外键的表:父行先插。Kahn 排序,环则报错。"""
    def key_of(row: dict) -> tuple:
        return tuple(row[column] for column in pk_columns)

    index = {key_of(row): row for row in rows}
    to_positions = {name: position for position, name in enumerate(pk_columns)}
    parents: dict[tuple, list[tuple]] = {key: [] for key in index}
    for row in rows:
        for fk in self_fks:
            # pairs 的 from 是本表列,to 是被引用的(同表)主键列;
            # 按 to 在主键中的位置槽位组装父行主键,避免列序假设。
            slots: list = [None] * len(pk_columns)
            for pair in fk["pairs"]:
                position = to_positions.get(pair["to"])
                if position is None:
                    fail(f"{table}:自引用外键引用了非主键列 {pair['to']},无法排序。")
                slots[position] = row[pair["from"]]
            if any(part is None for part in slots):
                continue
            parent_key = tuple(slots)
            if parent_key in index and parent_key != key_of(row):
                parents[key_of(row)].append(parent_key)
    ordered: list[dict] = []
    emitted: set[tuple] = set()
    pending = list(index)
    while pending:
        progressed = False
        next_pending = []
        for key in pending:
            if all(parent in emitted for parent in parents[key]):
                ordered.append(index[key])
                emitted.add(key)
                progressed = True
            else:
                next_pending.append(key)
        if not progressed:
            fail(f"{table}:自引用外键存在环或父行缺失,无法确定插入顺序;未决行主键:{next_pending[:5]}")
        pending = next_pending
    return ordered


def migrate(args: argparse.Namespace) -> None:
    source = introspect_sqlite(args.sqlite)
    if args.check_source:
        print_source_report(source, args.sqlite)
        print("\n--check-source:仅内省,不连接 PG。")
        source["conn"].close()
        return

    try:
        import psycopg2
        import psycopg2.extras
        from psycopg2 import sql
    except ImportError:
        fail("缺少 psycopg2:请用 uv run --with psycopg2-binary python scripts/drill_migrate_sqlite_to_pg.py ...")

    dsn = normalize_pg_dsn(args.pg_url)
    print("停写确认:开始搬迁前,必须已停止一切写源 SQLite 的服务/进程。")
    print("目标 PG 中已有数据的表只会追加;主键冲突会直接报错。")
    if not args.yes:
        fail("未加 --yes,拒绝执行。确认已停写后重跑并加 --yes。")

    try:
        pg = psycopg2.connect(dsn, connect_timeout=15)
    except psycopg2.Error as exc:
        fail(f"无法连接 PG(检查 PG_DRILL_URL、网络白名单、sslmode):{exc}")
    pg.autocommit = False

    report: list[dict] = []
    try:
        with pg.cursor() as cursor:
            pg_tables = set()
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                (args.schema,),
            )
            for (name,) in cursor.fetchall():
                pg_tables.add(name)

        for table in source["order"]:
            meta = source["tables"][table]
            if table not in pg_tables:
                print(f"[SKIP] {table}:目标 PG(schema={args.schema})中不存在该表,跳过。")
                report.append({"table": table, "source": meta["row_count"], "inserted": 0,
                               "target": None, "status": "SKIP(目标无此表)"})
                continue

            with pg.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s",
                    (args.schema, table),
                )
                pg_columns = {name: data_type for name, data_type in cursor.fetchall()}
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(args.schema), sql.Identifier(table)
                    )
                )
                pre_existing = cursor.fetchone()[0]

            source_columns = [column["name"] for column in meta["columns"]]
            missing_in_pg = [c for c in source_columns if c not in pg_columns]
            if missing_in_pg:
                fail(f"{table}:源库列在目标表中不存在:{missing_in_pg};"
                     "目标表应由 alembic 建好且与源 schema 对齐,请检查迁移版本。")
            shared = [c for c in source_columns if c in pg_columns]

            rows = [
                dict(zip(source_columns, row, strict=True))
                for row in source["conn"].execute(
                    f"SELECT {', '.join(chr(34) + c + chr(34) for c in source_columns)} FROM \"{table}\""
                )
            ]
            self_fks = [fk for fk in meta["fks"] if fk["table"] == table]
            if self_fks:
                pk_columns = [
                    c["name"]
                    for c in sorted((c for c in meta["columns"] if c["pk"]), key=lambda c: c["pk"])
                ]
                rows = order_rows_for_self_fk(rows, pk_columns, self_fks, table)

            converted_rows = []
            for row in rows:
                values = []
                for column in shared:
                    kind, value = convert_value(row[column], pg_columns[column], table, column)
                    values.append(psycopg2.extras.Json(value) if kind == "json" else value)
                converted_rows.append(tuple(values))

            inserted = 0
            if converted_rows:
                insert_sql = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(args.schema),
                    sql.Identifier(table),
                    sql.SQL(", ").join(sql.Identifier(c) for c in shared),
                    sql.SQL(", ").join(sql.Placeholder() for _ in shared),
                )
                try:
                    with pg.cursor() as cursor:
                        cursor.executemany(insert_sql, converted_rows)
                        inserted = len(converted_rows)
                    pg.commit()
                except psycopg2.Error as exc:
                    pg.rollback()
                    fail(f"{table}:INSERT 失败(事务已回滚):{exc}\n"
                         "常见原因:目标表非空导致主键冲突;检查约束(check/FK)不满足;"
                         "列类型与迁移版本不匹配。")

            with pg.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(args.schema), sql.Identifier(table)
                    )
                )
                target_rows = cursor.fetchone()[0]
            status = "OK" if (inserted == meta["row_count"] and target_rows == pre_existing + inserted) else "MISMATCH"
            report.append({"table": table, "source": meta["row_count"], "inserted": inserted,
                           "target": target_rows, "status": status, "pre_existing": pre_existing})
    finally:
        source["conn"].close()
        pg.close()

    print("\n行数对账报告:")
    print(f"{'table':32} {'source':>8} {'inserted':>9} {'target':>8}  status")
    bad = False
    for item in report:
        target = "-" if item["target"] is None else str(item["target"])
        print(f"{item['table']:32} {item['source']:>8} {item['inserted']:>9} {target:>8}  {item['status']}")
        if item["status"] not in ("OK", "SKIP(目标无此表)"):
            bad = True
    if bad:
        fail("对账未全部通过:请检查上面 MISMATCH 的表。")
    print("\n搬迁完成:全部表行数对账一致。下一步跑双跑脚本 scripts/drill_cross_db_double_run.py。")


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 换库演练搬迁(小数据量)")
    parser.add_argument("--sqlite", required=True, help="源 SQLite 文件路径")
    parser.add_argument("--schema", default="public", help="目标 PG schema,默认 public")
    parser.add_argument("--yes", action="store_true", help="确认已停写,真正执行搬迁")
    parser.add_argument("--check-source", action="store_true",
                        help="只内省源库并打印结构/行数报告,不连接 PG")
    args = parser.parse_args()
    args.sqlite = Path(args.sqlite)
    args.pg_url = os.environ.get("PG_DRILL_URL", "")
    migrate(args)


if __name__ == "__main__":
    main()

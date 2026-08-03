"""
S0.2 建库脚本：一条命令在 SQLite 或 PostgreSQL 上建出
concepts / rules / clients / diagnoses / execution_log 五张表。

用法：
    uv run python scripts/init_db.py --db sqlite:///local.db
    uv run python scripts/init_db.py --db postgresql+psycopg2://user:pass@host:5432/dbname

默认只补建缺失的表，不清空已有数据。加 --reset 才会先清空已有表再重建
（Murat 审查意见：破坏性操作不该是无提示默认值，库里一旦有真实卡片数据，
误跑裸命令会静默丢数据——想清空必须显式加 --reset，且会打印警告）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 直接用 `python scripts/init_db.py` 跑时，Python 只把 scripts/ 目录放进 sys.path，
# 项目根目录（campaign_optimizer 包所在处）不在其中，这里手动补上。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认用系统代码页（GBK）输出，中文会乱码，这里强制走 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import inspect

from campaign_optimizer.ontology.db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化本体运行时数据库（五张表）")
    parser.add_argument(
        "--db",
        default="sqlite:///local.db",
        help="SQLAlchemy 连接串，默认本地 SQLite 文件 local.db",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="清空已有表再重建（默认不清空，只补建缺失的表）",
    )
    args = parser.parse_args()

    if args.reset:
        print(f"[init_db] --reset 已启用：即将清空 {args.db} 上的全部现有数据后重建。")

    engine = init_db(args.db, drop_first=args.reset)
    with engine.connect() as conn:
        table_names = sorted(inspect(conn).get_table_names())
    print(f"[init_db] 已在 {args.db} 建好 {len(table_names)} 张表：{table_names}")


if __name__ == "__main__":
    main()

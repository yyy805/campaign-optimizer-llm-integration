"""演练第 3a 步：在目标 PG 库创建五张遗留基础表（幂等，只补缺不覆盖）。

用法（PowerShell 新窗口）：
  cd "D:\AAA Data science and AI for busines\AI-projects\bmad\.worktrees\hannah-convergence"
  $env:PG_DRILL_URL="postgresql://用户:化妆后密码@地址:5432/mta_data?sslmode=prefer&connect_timeout=10"
  uv run --with psycopg2-binary python "D:\AAA Data science and AI for busines\AI-projects\bmad\scripts\drill_create_legacy_tables.py"
"""
import os
import sys

WORKTREE_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".worktrees", "hannah-convergence",
)
sys.path.insert(0, os.path.abspath(WORKTREE_ROOT))

url = os.environ.get("PG_DRILL_URL") or os.environ.get("ONTOLOGY_DATABASE_URL")
if not url:
    print("未设置 PG_DRILL_URL，请先用 $env:PG_DRILL_URL=... 设置")
    sys.exit(1)

from campaign_optimizer.ontology.db import (
    Base,
    ClientRow,
    ConceptRow,
    DiagnosisRow,
    ExecutionLogRow,
    RuleRow,
)
from sqlalchemy import create_engine

engine = create_engine(url)
Base.metadata.create_all(
    engine,
    tables=[
        ConceptRow.__table__,
        RuleRow.__table__,
        ClientRow.__table__,
        DiagnosisRow.__table__,
        ExecutionLogRow.__table__,
    ],
)
print("legacy 5 tables ready")

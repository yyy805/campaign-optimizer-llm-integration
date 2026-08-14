"""演练第 4 步预备：生成标准空 SQLite 源库（13 张表、0 行数据），供空搬排练。

用法（PowerShell，worktree 根目录下）：
  uv run --with psycopg2-binary --with alembic python "D:\AAA Data science and AI for busines\AI-projects\bmad\scripts\drill_make_sqlite_source.py" drill_source.db
"""
import os
import subprocess
import sys

WORKTREE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".worktrees", "hannah-convergence")
)
sys.path.insert(0, WORKTREE_ROOT)

target = sys.argv[1] if len(sys.argv) > 1 else "drill_source.db"
if os.path.exists(target):
    os.remove(target)

from campaign_optimizer.ontology.db import (
    Base,
    ClientRow,
    ConceptRow,
    DiagnosisRow,
    ExecutionLogRow,
    RuleRow,
)
from sqlalchemy import create_engine

engine = create_engine(f"sqlite:///{target}")
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
print("legacy 5 tables ready (sqlite)")

env = dict(os.environ, ONTOLOGY_DATABASE_URL=f"sqlite:///{os.path.abspath(target)}")
result = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=WORKTREE_ROOT,
    env=env,
)
if result.returncode == 0:
    print("runtime 8 tables ready (sqlite)，空源库完成:", target)
sys.exit(result.returncode)

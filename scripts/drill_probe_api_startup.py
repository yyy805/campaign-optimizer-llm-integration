"""演练诊断：对测试库跑 API 自带的数据库初始化+迁移，把被吞掉的真实错误完整打印。

用法（PowerShell 新窗口，必须在 api 目录下用它的依赖跑）：
  cd "D:\AAA Data science and AI for busines\AI-projects\bmad\.worktrees\hannah-convergence\ontology review api"
  $env:TEST_POSTGRES_URL="postgresql+psycopg://用户:化妆后密码@地址:5432/mta_data_test?sslmode=prefer"
  uv run python "D:\AAA Data science and AI for busines\AI-projects\bmad\scripts\drill_probe_api_startup.py"
"""
import os
import sys
import traceback

API_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", ".worktrees", "hannah-convergence", "ontology review api",
)
WORKTREE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".worktrees", "hannah-convergence")
sys.path.insert(0, os.path.abspath(API_DIR))
sys.path.insert(0, os.path.abspath(WORKTREE_ROOT))

url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("PG_DRILL_URL")
if not url:
    print("未设置 TEST_POSTGRES_URL 或 PG_DRILL_URL")
    sys.exit(1)

try:
    from app.main import initialize_database

    db = initialize_database(url)
    print("API 数据库初始化 + 迁移：OK")
    db.close()
except Exception:
    traceback.print_exc()
    print("== 初始化失败，上面是完整真实错误 ==")
    sys.exit(1)

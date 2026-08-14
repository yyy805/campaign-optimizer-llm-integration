"""演练辅助：创建 mta_data_test 测试库（幂等，已存在则跳过）。

用法（PowerShell 新窗口）：
  cd "D:\AAA Data science and AI for busines\AI-projects\bmad"
  $env:PG_DRILL_URL="postgresql://用户:化妆后密码@地址:5432/库名?sslmode=prefer"
  uv run --with psycopg2-binary python scripts/drill_make_test_db.py
"""
import os
import sys

import psycopg2

url = os.environ.get("PG_DRILL_URL")
if not url:
    print("未设置 PG_DRILL_URL，请先用 $env:PG_DRILL_URL=... 设置")
    sys.exit(1)

conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname='mta_data_test'")
if cur.fetchone() is None:
    cur.execute("CREATE DATABASE mta_data_test")
    print("test库建好")
else:
    print("test库已存在")
conn.close()

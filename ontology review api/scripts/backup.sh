#!/usr/bin/env sh
set -eu

database_path="${DATABASE_PATH:-./data/review.db}"
backup_dir="${BACKUP_DIR:-./backups}"
test -f "$database_path" || { echo "Database does not exist: $database_path" >&2; exit 1; }
mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/review-$timestamp.db"
if test -e "$target"; then
  target="$backup_dir/review-$timestamp-$$.db"
fi
python3 - "$database_path" "$target" <<'PY'
import sqlite3, sys
source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
source.close(); target.close()
PY
echo "$target"

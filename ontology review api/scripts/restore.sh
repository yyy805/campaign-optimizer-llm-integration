#!/usr/bin/env sh
set -eu

backup_path="${1:?Usage: restore.sh BACKUP_PATH TARGET_PATH}"
target_path="${2:?Usage: restore.sh BACKUP_PATH TARGET_PATH}"
test -f "$backup_path" || { echo "Backup does not exist: $backup_path" >&2; exit 1; }
test ! -e "$target_path" || { echo "Refusing to overwrite existing target: $target_path" >&2; exit 1; }
mkdir -p "$(dirname "$target_path")"
temp_path="$(mktemp "${target_path}.restore.XXXXXX")"
trap 'rm -f "$temp_path"' EXIT
python3 - "$backup_path" "$temp_path" "$target_path" <<'PY'
import os, sqlite3, sys
source = sqlite3.connect(sys.argv[1])
assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
source.close(); target.close()
assert sqlite3.connect(sys.argv[2]).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
os.link(sys.argv[2], sys.argv[3])
os.unlink(sys.argv[2])
PY
trap - EXIT
echo "$target_path"

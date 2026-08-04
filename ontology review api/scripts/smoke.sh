#!/usr/bin/env sh
set -eu

base_url="${BASE_URL:-http://127.0.0.1:8000}"
api_key="${SMOKE_API_KEY:-demo-agent-key}"
python_bin="${PYTHON_BIN:-python3}"
temp_dir="$(mktemp -d)"
state_file="${SMOKE_STATE_FILE:-/tmp/ontology-review-smoke-review-id}"
trap 'rm -rf "$temp_dir"' EXIT

curl --fail --silent --show-error "$base_url/health" > "$temp_dir/health.json"
curl --fail --silent --show-error "$base_url/ready" > "$temp_dir/ready.json"

if test "${1:-}" = "--verify-persistence"; then
  test -s "$state_file" || { echo "No prior smoke Review ID found at $state_file" >&2; exit 1; }
  review_id="$(sed -n '1p' "$state_file")"
  curl --fail --silent --show-error -H "X-API-Key: $api_key" \
    "$base_url/api/v1/reviews/$review_id" > "$temp_dir/persisted.json"
  "$python_bin" - "$temp_dir/persisted.json" "$review_id" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["review_id"] == sys.argv[2], body
print(f'Persistence verified: {body["review_id"]}')
PY
  printf '%s' '{"client_id":"demo_client_001","entity":{"grain":"touchpoint","id":"smoke-r3"},"candidate_rules":["R3"],"inputs":[{"concept":"mta_roas","value":1.6,"baseline":1.0}],"expected_ontology_version":"v1.1-demo"}' > "$temp_dir/replay-match.json"
  curl --fail --silent --show-error \
    -H "X-API-Key: $api_key" \
    -H "Idempotency-Key: smoke-match" \
    -H "Content-Type: application/json" \
    --data-binary "@$temp_dir/replay-match.json" \
    "$base_url/api/v1/reviews" > "$temp_dir/replayed.json"
  "$python_bin" - "$temp_dir/replayed.json" "$review_id" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["review_id"] == sys.argv[2], body
print(f'Post-restart idempotency replay verified: {body["review_id"]}')
PY
  exit 0
fi

post_review() {
  case_name="$1"
  expected="$2"
  payload_file="$3"
  curl --fail --silent --show-error \
    -H "X-API-Key: $api_key" \
    -H "Idempotency-Key: smoke-$case_name" \
    -H "Content-Type: application/json" \
    --data-binary "@$payload_file" \
    "$base_url/api/v1/reviews" > "$temp_dir/$case_name-response.json"
  "$python_bin" - "$temp_dir/$case_name-response.json" "$expected" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["outcome"] == sys.argv[2], body
print(f'{body["review_id"]} {body["outcome"]}')
PY
}

printf '%s' '{"client_id":"demo_client_001","entity":{"grain":"touchpoint","id":"smoke-r3"},"candidate_rules":["R3"],"inputs":[{"concept":"mta_roas","value":1.6,"baseline":1.0}],"expected_ontology_version":"v1.1-demo"}' > "$temp_dir/match.json"
printf '%s' '{"client_id":"demo_client_001","entity":{"grain":"campaign","id":"smoke-conflict"},"candidate_rules":["R1","R2"],"inputs":[{"concept":"acos","value":0.5,"baseline":0.35},{"concept":"ctr","value":0.01,"baseline":0.02},{"concept":"impressions_growth","value":0.25}]}' > "$temp_dir/conflict.json"
printf '%s' '{"client_id":"demo_client_001","entity":{"grain":"campaign","id":"smoke-r7"},"candidate_rules":["R7"],"inputs":[]}' > "$temp_dir/no-coverage.json"

post_review "match" "MATCH" "$temp_dir/match.json"
"$python_bin" - "$temp_dir/match-response.json" "$state_file" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
open(sys.argv[2], "w", encoding="utf-8").write(body["review_id"] + "\n")
PY
post_review "conflict" "CONFLICT" "$temp_dir/conflict.json"
post_review "no-coverage" "NO_COVERAGE" "$temp_dir/no-coverage.json"
echo "Smoke test passed: health, readiness, MATCH, CONFLICT, NO_COVERAGE"

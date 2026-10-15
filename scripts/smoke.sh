#!/usr/bin/env bash
# Smoke test a running URL shortener: health, shorten, redirect, stats. Usage: scripts/smoke.sh http://localhost:8080
set -euo pipefail
BASE="${1:?usage: smoke.sh BASE_URL}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

curl -fsS "$BASE/health" | grep -q '"status": "ok"' || fail "/health"
CODE=$(curl -fsS -X POST "$BASE/shorten" -H 'Content-Type: application/json' -d '{"url":"https://example.com/smoke"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])') || fail "/shorten"
LOC=$(curl -sS -o /dev/null -w '%{redirect_url}' "$BASE/$CODE")
[ "$LOC" = "https://example.com/smoke" ] || fail "redirect gave '$LOC'"
curl -fsS "$BASE/stats/$CODE" | grep -q '"hits": 1' || fail "/stats"
echo "SMOKE OK: $BASE"

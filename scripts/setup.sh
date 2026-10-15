#!/usr/bin/env bash
# One-shot local setup: venv, Flask checkout, FalkorDB container.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip -q install -r requirements.txt
[ -d targets/flask ] || git clone -q https://github.com/pallets/flask.git targets/flask
if ! docker ps --format '{{.Ports}}' | grep -q '6379->6379'; then
  docker run -d --rm --name keel-falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest
fi
echo "ready: . .venv/bin/activate && python -m keel.evaluate --rebuild"

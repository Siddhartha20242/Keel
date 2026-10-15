#!/usr/bin/env bash
# Open a demo PR on this repo so the pipeline runs live. It posts a public PR, so run it on purpose.
#   scripts/live_demo.sh break    # inverts the dedupe check: tests fail, gate blocks, the Fixer reacts
#   scripts/live_demo.sh docs     # harmless docstring change: the gate asks for one approval
#   scripts/live_demo.sh clean    # close every open demo/live-* PR and delete its branch
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
MODE="${1:?usage: live_demo.sh break|docs|clean}"

if [ "$MODE" = clean ]; then
  for pr in $(gh pr list --repo "$REPO" --state open --json number,headRefName --jq '.[]|select(.headRefName|startswith("demo/live-"))|.number'); do
    gh pr close "$pr" --repo "$REPO" --delete-branch >/dev/null && echo "closed #$pr"
  done
  exit 0
fi

BRANCH="demo/live-$MODE-$(date +%H%M%S)"
W="$(mktemp -d)"
git fetch -q origin main
git worktree add -q -b "$BRANCH" "$W" origin/main
trap 'git worktree remove --force "$W" 2>/dev/null || true' EXIT
cd "$W"
case "$MODE" in
  break)
    sed -i.bak 's|if url in self._by_url:|if url not in self._by_url:|' demo-app/src/shortener/store.py && rm demo-app/src/shortener/store.py.bak
    git add demo-app/src/shortener/store.py; TITLE="store: simplify the dedupe check (live demo)"
    BODY="Live demo: looks like a simplification but inverts the dedupe condition. Expect tests to fail, the gate to block, and the Fixer to react." ;;
  docs)
    sed -i.bak 's|"""Base62 of a non-negative integer."""|"""Base62 of a non-negative integer (0 encodes to "0")."""|' demo-app/src/shortener/codes.py && rm demo-app/src/shortener/codes.py.bak
    git add demo-app/src/shortener/codes.py; TITLE="docs: say what encode(0) returns (live demo)"
    BODY="Live demo: a harmless docstring change. Expect the gate to ask for one human approval." ;;
  *) echo "unknown mode $MODE"; exit 2 ;;
esac
git commit -q -m "$TITLE"
git push -q -u origin "$BRANCH"
URL="$(gh pr create --repo "$REPO" --base main --head "$BRANCH" --title "$TITLE" --body "$BODY")"
echo "$URL"
echo "watch:  gh pr checks ${URL##*/} --repo $REPO --watch"

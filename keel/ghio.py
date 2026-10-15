"""PR comments via the `gh` CLI (preinstalled on GitHub runners). The marker + author check is what stops anyone but the
pipeline's own bot from speaking as the Skeptic: the gate ignores every other comment."""
from __future__ import annotations
import json, subprocess

MARK = {"skeptic": "<!-- keel:skeptic -->", "report": "<!-- keel:report -->"}
TRUSTED_AUTHOR = "github-actions[bot]"


def _gh(*args, stdin: str | None = None) -> str:
    p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def list_comments(repo: str, pr: int | str) -> list[dict]:
    out = _gh("api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp")
    pages = json.loads(out or "[]")
    return [c for page in pages for c in (page if isinstance(page, list) else [page])]


def latest_trusted(comments: list[dict], marker: str, author: str = TRUSTED_AUTHOR) -> dict | None:
    """Newest comment that carries `marker` AND was written by `author`. Everything else is ignored."""
    ok = [c for c in comments if (c.get("user") or {}).get("login") == author and marker in (c.get("body") or "")]
    return max(ok, key=lambda c: c.get("created_at", "")) if ok else None


def upsert_comment(repo: str, pr: int | str, kind: str, body: str) -> str:
    marker = MARK[kind]
    body = f"{marker}\n{body}"
    existing = latest_trusted(list_comments(repo, pr), marker)
    if existing:
        _gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", "-F", "body=@-", stdin=body)
        return "updated"
    _gh("api", f"repos/{repo}/issues/{pr}/comments", "-F", "body=@-", stdin=body)
    return "created"

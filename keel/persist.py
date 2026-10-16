"""The persistent FalkorDB store for the audit ledger and agent memory.

Configure with KEEL_STORE_HOST (and optionally KEEL_STORE_PORT, KEEL_STORE_USERNAME, KEEL_STORE_PASSWORD, KEEL_STORE_SSL=1), for example
a FalkorDB Cloud instance or any FalkorDB you keep running. With none set there is no persistent store and Keel falls back to the
JSONL files on the `keel-memory` branch. Those files stay as a backup either way: they are rewritten from the graph after every write.

Credentials are only passed to trusted jobs (memory.yml runs main's code, never a PR's). PR-time analysis reads the JSONL snapshot.
"""
from __future__ import annotations
import os

def repo_name() -> str:
    """Graph names are keel_audit_<repo> and keel_mem_<repo>."""
    return os.environ.get("KEEL_STORE_REPO", "demoapp")


def persistent_db():
    host = os.environ.get("KEEL_STORE_HOST", "").strip()
    if not host:
        return None
    from falkordb import FalkorDB
    db = FalkorDB(host=host, port=int(os.environ.get("KEEL_STORE_PORT") or 6379),
                  username=os.environ.get("KEEL_STORE_USERNAME") or None, password=os.environ.get("KEEL_STORE_PASSWORD") or None,
                  ssl=os.environ.get("KEEL_STORE_SSL") == "1")
    db.list_graphs()          # fail now, not halfway through a write
    return db

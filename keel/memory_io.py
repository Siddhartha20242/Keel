"""Agent memory that survives CI runs: attempts live in a plain JSONL file on a `keel-memory` branch (auditable, diffable),
and are loaded into the per-repo memory graph at the start of each analysis."""
from __future__ import annotations
import json, pathlib



def read_records(path: pathlib.Path | None) -> list[dict]:
    if not path or not pathlib.Path(path).exists():
        return []
    out = []
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue            # a corrupt line must not take the analysis down
    return out


def append_record(path: pathlib.Path, rec: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def load_into_graph(db, repo: str, records: list[dict]) -> int:
    from . import graph as G          # lazy: the memory CLI runs in a job with no third-party packages
    g = db.select_graph(G.memory_graph_name(repo))
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph(G.memory_graph_name(repo))
    q = G.read_query("mem_remember")
    n = 0
    for r in records:
        if not r.get("symbols"):
            continue
        g.query(q, {"id": str(r.get("id", n)), "issue": str(r.get("title", ""))[:200], "summary": str(r.get("summary", ""))[:500],
                    "outcome": r.get("outcome", "unknown"), "lane": r.get("lane", ""), "at": r.get("at", ""), "qnames": r["symbols"],
                    "json": json.dumps(r, sort_keys=True)})
        n += 1
    return n


def memory_graph(db, repo: str):
    from . import graph as G
    return db.select_graph(G.memory_graph_name(repo))


def record_attempt(db, repo: str, rec: dict) -> None:
    """Write one attempt into the (persistent) memory graph without clearing it."""
    from . import graph as G
    if not rec.get("symbols"):
        return
    memory_graph(db, repo).query(G.read_query("mem_remember"), {
        "id": str(rec.get("id", "")), "issue": str(rec.get("title", ""))[:200], "summary": str(rec.get("summary", ""))[:500],
        "outcome": rec.get("outcome", "unknown"), "lane": rec.get("lane", ""), "at": rec.get("at", ""), "qnames": rec["symbols"],
        "json": json.dumps(rec, sort_keys=True)})


def read_attempts_graph(db, repo: str) -> list[dict]:
    """Every recorded attempt, oldest first, as the full records the JSONL backup holds."""
    try:
        rows = memory_graph(db, repo).ro_query("MATCH (a:Attempt) WHERE a.json IS NOT NULL RETURN DISTINCT a.json, a.at, a.id ORDER BY a.at, a.id").result_set
    except Exception:
        return []
    return [json.loads(r[0]) for r in rows]


def write_all(path, recs: list[dict]) -> None:
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")

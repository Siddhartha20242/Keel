"""Tamper-evident audit ledger. Every agent action on a PR (analysis, Skeptic verdict, gate decision, report, merge) is a record;
each record carries the hash of the one before it, so editing or removing an old record breaks every hash after it.

The ledger is a JSONL file on the `keel-memory` branch (auditable with plain git). It is loaded into a FalkorDB graph on demand:

    (:PullRequest)-[:TOUCHES]->(:File)      (:PullRequest)-[:HAS_RUN]->(:AgentRun)-[:PRODUCED]->(:Verdict)
    (:AgentRun)-[:NEXT]->(:AgentRun)        the hash chain, in order

    python -m keel.ledger verify --file ledger.jsonl
    python -m keel.ledger explain --file ledger.jsonl --pr 7        # every action behind that PR, walked through the graph
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib

GENESIS = "0" * 64


def canonical(rec: dict) -> str:
    return json.dumps({k: v for k, v in rec.items() if k != "hash"}, sort_keys=True, separators=(",", ":"))


def digest(rec: dict) -> str:
    return hashlib.sha256(canonical(rec).encode()).hexdigest()


def read(path) -> list[dict]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def chain(entries: list[dict], prev: str = GENESIS, last_seq: int = -1) -> list[dict]:
    """Link `entries` after a record with hash `prev` and sequence `last_seq`. Each entry: kind, pr, actor, summary, data."""
    out, seq = [], last_seq
    for e in entries:
        seq += 1
        rec = {"seq": seq, "prev": prev, **e}
        rec["hash"] = digest(rec)
        prev = rec["hash"]
        out.append(rec)
    return out


def append(path, entries: list[dict]) -> list[dict]:
    """JSONL ledger: chain `entries` after the current end of the file and append them."""
    recs = read(path)
    out = chain(entries, recs[-1]["hash"] if recs else GENESIS, recs[-1]["seq"] if recs else -1)
    with open(path, "a") as f:
        for r in out:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return out


def write_all(path, recs: list[dict]) -> None:
    """Rewrite the JSONL backup from the graph (the graph is the source of truth when a store is configured)."""
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def verify(recs: list[dict]) -> tuple[bool, str]:
    """True if the chain is intact. Otherwise says where it first breaks."""
    prev = GENESIS
    for i, r in enumerate(recs):
        if r.get("seq") != i:
            return False, f"record {i}: sequence is {r.get('seq')} (a record was removed or reordered)"
        if r.get("prev") != prev:
            return False, f"record {i}: previous-hash does not match (an earlier record changed)"
        if digest(r) != r.get("hash"):
            return False, f"record {i}: contents do not match their hash (this record was edited)"
        prev = r["hash"]
    return True, f"{len(recs)} records, chain intact"


def stage_entries(blob: dict, outcome: str) -> list[dict]:
    """The records one PR leaves behind, in the order the stages ran, built from the Reporter's data blob."""
    common = {"pr": int(blob["pr"]), "at": blob.get("at", ""), "run": str(blob.get("run", ""))}
    return [
        {**common, "kind": "analyze", "actor": "keel-analyze",
         "summary": f"{len(blob['symbols'])} symbol(s) edited; graph selected {blob.get('tests_selected', 0)} tests; lane {blob['lane']}",
         "data": {"edited": blob["symbols"], "files": blob.get("files", []), "tests_selected": blob.get("tests_selected", 0),
                  "new_failures": blob.get("new_failures", []), "risk": blob["risk"], "lane": blob["lane"]}},
        {**common, "kind": "skeptic", "actor": "github-actions[bot]", "summary": f"Skeptic verdict: RISK {blob['skeptic_risk']}", "data": {"risk": blob["skeptic_risk"], "findings": blob.get("findings", [])}},
        {**common, "kind": "gate", "actor": "keel-risk-gate", "summary": blob.get("decision", "no decision recorded"),
         "data": {"level": blob.get("level", ""), "graph_lane": blob.get("graph_lane", blob["lane"])}},
        {**common, "kind": "outcome", "actor": "human", "summary": f"PR {outcome} by a person", "data": {"outcome": outcome, "title": blob.get("title", "")}},
    ]


def audit_graph(db, repo: str, fresh: bool = False):
    name = f"keel_audit_{repo}"
    if fresh:
        try:
            db.select_graph(name).delete()
        except Exception:
            pass
    g = db.select_graph(name)
    for q in ("CREATE INDEX FOR (r:AgentRun) ON (r.seq)", "CREATE INDEX FOR (p:PullRequest) ON (p.number)"):
        try:
            g.query(q)
        except Exception:
            pass                  # already there
    return g


def insert(g, r: dict, prev_seq: int | None) -> None:
    """One record as graph nodes and edges. The full record rides along as JSON on the AgentRun, so the chain can be verified from the graph."""
    d = r.get("data", {})
    g.query("""MERGE (p:PullRequest {number: $pr})
               CREATE (a:AgentRun {seq: $seq, kind: $kind, actor: $actor, summary: $summary, hash: $short, full: $full, at: $at, run: $run, rec: $rec})
               CREATE (p)-[:HAS_RUN]->(a)""",
            {"pr": r["pr"], "seq": r["seq"], "kind": r["kind"], "actor": r["actor"], "summary": r["summary"], "short": r["hash"][:12], "full": r["hash"],
             "at": r.get("at", ""), "run": r.get("run", ""), "rec": json.dumps(r, sort_keys=True)})
    if r["kind"] in ("skeptic", "gate", "outcome"):
        verdict = d.get("risk") or d.get("level") or d.get("outcome") or ""
        g.query("MATCH (a:AgentRun {seq: $seq}) CREATE (a)-[:PRODUCED]->(:Verdict {kind: $kind, value: $value})", {"seq": r["seq"], "kind": r["kind"], "value": str(verdict)})
    for i, fd in enumerate(d.get("findings", [])):
        key = f"{r['pr']}-{r['seq']}-{i}"       # unique per record, so two PRs (or two runs) never share a Finding
        g.query("MATCH (a:AgentRun {seq: $seq}) CREATE (n:Finding {key: $key, pr: $pr, idx: $i, text: $text}) CREATE (a)-[:PRODUCED]->(n)",
                {"seq": r["seq"], "key": key, "pr": r["pr"], "i": i, "text": fd.get("text", "")})
        if fd.get("file"):
            g.query("MATCH (n:Finding {key: $key}) MERGE (f:File {path: $f}) CREATE (n)-[:ABOUT]->(f)", {"key": key, "f": fd["file"]})
        if fd.get("test"):
            g.query("MATCH (n:Finding {key: $key}) MERGE (t:Test {name: $t}) CREATE (n)-[:SUGGESTS]->(t)", {"key": key, "t": fd["test"]})
    for f in d.get("files", []):
        g.query("MATCH (p:PullRequest {number: $pr}) MERGE (f:File {path: $f}) MERGE (p)-[:TOUCHES]->(f)", {"pr": r["pr"], "f": f})
    if prev_seq is not None:
        g.query("MATCH (a:AgentRun {seq: $a}), (b:AgentRun {seq: $b}) CREATE (a)-[:NEXT]->(b)", {"a": prev_seq, "b": r["seq"]})


def load_into_graph(db, repo: str, recs: list[dict]) -> str:
    """Replace the audit graph with `recs` (a snapshot, e.g. from the JSONL file)."""
    g = audit_graph(db, repo, fresh=True)
    prev = None
    for r in recs:
        insert(g, r, prev)
        prev = r["seq"]
    return f"keel_audit_{repo}"


def read_graph(db, repo: str) -> list[dict]:
    """The ledger as stored in the graph, in order."""
    g = db.select_graph(f"keel_audit_{repo}")
    try:
        rows = g.ro_query("MATCH (a:AgentRun) RETURN a.rec ORDER BY a.seq").result_set
    except Exception:
        return []
    return [json.loads(r[0]) for r in rows if r[0]]


def verify_graph(db, repo: str) -> tuple[bool, str]:
    """Verify the chain from what is stored in the graph: record hashes, links, and that every NEXT edge is actually there."""
    recs = read_graph(db, repo)
    ok, msg = verify(recs)
    if not ok or not recs:
        return ok, msg + (" (verified from the graph)" if ok else "")
    g = db.select_graph(f"keel_audit_{repo}")
    for r in g.ro_query("MATCH (a:AgentRun) RETURN a.seq, a.full").result_set:
        if recs[r[0]]["hash"] != r[1]:
            return False, f"record {r[0]}: the stored hash property does not match the record"
    edges = g.ro_query("MATCH (:AgentRun)-[n:NEXT]->(:AgentRun) RETURN count(n)").result_set[0][0]
    if edges != max(len(recs) - 1, 0):
        return False, f"{edges} NEXT edges for {len(recs)} records (an edge was added or removed)"
    return True, f"{len(recs)} records, chain intact (verified from the graph)"


def append_graph(db, repo: str, entries: list[dict]) -> list[dict]:
    """Extend the ledger in the graph. Refuses to extend a chain that does not verify."""
    ok, msg = verify_graph(db, repo)
    if not ok:
        raise RuntimeError(f"refusing to extend a broken ledger: {msg}")
    g = audit_graph(db, repo)
    head = g.ro_query("MATCH (a:AgentRun) RETURN a.seq, a.full ORDER BY a.seq DESC LIMIT 1").result_set
    recs = chain(entries, head[0][1] if head else GENESIS, head[0][0] if head else -1)
    prev = head[0][0] if head else None
    for r in recs:
        insert(g, r, prev)
        prev = r["seq"]
    return recs


def explain(db, repo: str, pr: int) -> list[dict]:
    """Every action behind a PR, in order, each with the verdict it produced: a walk over HAS_RUN and PRODUCED edges."""
    from . import graph as G
    g = db.select_graph(f"keel_audit_{repo}")
    rows = g.ro_query(G.read_query("explain_pr"), {"pr": int(pr)}).result_set
    return [dict(zip(("seq", "kind", "actor", "summary", "at", "hash", "verdict"), r)) for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("verify", "explain"):
        p = sub.add_parser(name)
        p.add_argument("--file", help="a JSONL ledger file")
        p.add_argument("--store", action="store_true", help="read the persistent FalkorDB store (KEEL_STORE_*) instead of a file")
        p.add_argument("--repo", default="demoapp")
        if name == "explain":
            p.add_argument("--pr", required=True, type=int)
    a = ap.parse_args(argv)
    if a.store:
        from .persist import persistent_db
        db = persistent_db()
        if db is None:
            raise SystemExit("no persistent store configured (set KEEL_STORE_HOST)")
        ok, msg = verify_graph(db, a.repo)
    else:
        if not a.file:
            raise SystemExit("give --file or --store")
        recs = read(a.file)
        ok, msg = verify(recs)
    print(("OK: " if ok else "BROKEN: ") + msg)
    if a.cmd == "explain":
        from . import graph as G
        if not a.store:
            db = G.connect()
            load_into_graph(db, a.repo, recs)
        rows = explain(db, a.repo, a.pr)
        print(f"\nPR #{a.pr}: {len(rows)} recorded action(s)" + (" (from the persistent store)" if a.store else ""))
        for r in rows:
            print(f"  {r['seq']:>3}  {r['kind']:<8} {r['actor']:<22} {r['summary']}" + (f"  -> verdict: {r['verdict']}" if r["verdict"] else "") + f"   [{r['hash']}]")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

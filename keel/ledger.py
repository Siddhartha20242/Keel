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


def append(path, entries: list[dict]) -> list[dict]:
    """Chain `entries` after the current end of the ledger. Each entry: kind, pr, actor, summary, data (and optional run)."""
    recs = read(path)
    prev, seq = (recs[-1]["hash"], recs[-1]["seq"]) if recs else (GENESIS, -1)
    out = []
    for e in entries:
        seq += 1
        rec = {"seq": seq, "prev": prev, **e}
        rec["hash"] = digest(rec)
        prev = rec["hash"]
        out.append(rec)
    with open(path, "a") as f:
        for r in out:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return out


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
        {**common, "kind": "skeptic", "actor": "github-actions[bot]", "summary": f"Skeptic verdict: RISK {blob['skeptic_risk']}", "data": {"risk": blob["skeptic_risk"]}},
        {**common, "kind": "gate", "actor": "keel-risk-gate", "summary": blob.get("decision", "no decision recorded"),
         "data": {"level": blob.get("level", ""), "graph_lane": blob.get("graph_lane", blob["lane"])}},
        {**common, "kind": "outcome", "actor": "human", "summary": f"PR {outcome} by a person", "data": {"outcome": outcome, "title": blob.get("title", "")}},
    ]


def load_into_graph(db, repo: str, recs: list[dict]) -> str:
    from . import graph as G
    name = f"keel_audit_{repo}"
    try:
        db.select_graph(name).delete()
    except Exception:
        pass
    g = db.select_graph(name)
    g.query("CREATE INDEX FOR (r:AgentRun) ON (r.seq)")
    g.query("CREATE INDEX FOR (p:PullRequest) ON (p.number)")
    prev = None
    for r in recs:
        d = r.get("data", {})
        g.query("""MERGE (p:PullRequest {number: $pr})
                   CREATE (a:AgentRun {seq: $seq, kind: $kind, actor: $actor, summary: $summary, hash: $hash, at: $at, run: $run})
                   CREATE (p)-[:HAS_RUN]->(a)""",
                {"pr": r["pr"], "seq": r["seq"], "kind": r["kind"], "actor": r["actor"], "summary": r["summary"], "hash": r["hash"][:12], "at": r.get("at", ""), "run": r.get("run", "")})
        if r["kind"] in ("skeptic", "gate", "outcome"):
            verdict = d.get("risk") or d.get("level") or d.get("outcome") or ""
            g.query("MATCH (a:AgentRun {seq: $seq}) CREATE (a)-[:PRODUCED]->(:Verdict {kind: $kind, value: $value})", {"seq": r["seq"], "kind": r["kind"], "value": str(verdict)})
        for f in d.get("files", []):
            g.query("MATCH (p:PullRequest {number: $pr}) MERGE (f:File {path: $f}) MERGE (p)-[:TOUCHES]->(f)", {"pr": r["pr"], "f": f})
        if prev is not None:
            g.query("MATCH (a:AgentRun {seq: $a}), (b:AgentRun {seq: $b}) CREATE (a)-[:NEXT]->(b)", {"a": prev, "b": r["seq"]})
        prev = r["seq"]
    return name


def explain(db, repo: str, pr: int) -> list[dict]:
    """Every action behind a PR, in order, each with the verdict it produced: a walk over HAS_RUN and PRODUCED edges."""
    from . import graph as G
    g = db.select_graph(f"keel_audit_{repo}")
    rows = g.ro_query(G.read_query("explain_pr"), {"pr": int(pr)}).result_set
    return [dict(zip(("seq", "kind", "actor", "summary", "at", "hash", "verdict"), r)) for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--file", required=True)
    e = sub.add_parser("explain"); e.add_argument("--file", required=True); e.add_argument("--pr", required=True, type=int); e.add_argument("--repo", default="demoapp")
    a = ap.parse_args(argv)
    recs = read(a.file)
    ok, msg = verify(recs)
    print(("OK: " if ok else "BROKEN: ") + msg)
    if a.cmd == "explain":
        from . import graph as G
        db = G.connect()
        load_into_graph(db, a.repo, recs)
        rows = explain(db, a.repo, a.pr)
        print(f"\nPR #{a.pr}: {len(rows)} recorded action(s)")
        for r in rows:
            print(f"  {r['seq']:>3}  {r['kind']:<8} {r['actor']:<22} {r['summary']}" + (f"  -> verdict: {r['verdict']}" if r["verdict"] else "") + f"   [{r['hash']}]")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

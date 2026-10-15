"""FalkorDB schema + loader. One graph per (repo, commit) for code, one graph per repo for agent memory."""
from __future__ import annotations
import datetime as dt, re, pathlib
from falkordb import FalkorDB

QUERIES = pathlib.Path(__file__).resolve().parents[1] / "queries"


def connect(host="localhost", port=6379) -> FalkorDB:
    return FalkorDB(host=host, port=port)


def code_graph_name(repo: str, case_id: str) -> str:
    return f"keel_{repo}_{case_id}"


def memory_graph_name(repo: str) -> str:
    return f"keel_mem_{repo}"


def _batches(rows, n=500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _ts(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def load_code_graph(db, name, code, history, covers, meta):
    g = db.select_graph(name)
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph(name)
    for q in ("CREATE INDEX FOR (s:Symbol) ON (s.qname)", "CREATE INDEX FOR (s:Symbol) ON (s.name)",
              "CREATE INDEX FOR (f:File) ON (f.path)", "CREATE INDEX FOR (t:Test) ON (t.id)",
              "CREATE INDEX FOR (c:Commit) ON (c.sha)", "CREATE INDEX FOR (p:Person) ON (p.name)"):
        g.query(q)
    g.query("CREATE (:Meta {repo:$repo, ref:$ref, ref_ts:$ts})", {"repo": meta["repo"], "ref": meta["ref"], "ts": meta["ref_ts"]})
    for b in _batches(code["files"]):
        g.query("UNWIND $rows AS r CREATE (:File {path:r.path, kind:r.kind})", {"rows": b})
    syms = [s for s in code["symbols"] if s["kind"] != "test"]
    tests = [s for s in code["symbols"] if s["kind"] == "test"]
    for b in _batches(syms):
        g.query("""UNWIND $rows AS r CREATE (s:Symbol {qname:r.qname, name:r.name, kind:r.kind, path:r.path,
                   start:r.start, end:r.end, cls:r.cls, words:r.words})""", {"rows": b})
    for b in _batches(tests):
        g.query("""UNWIND $rows AS r CREATE (:Test {id:r.qname, name:r.name, path:r.path, start:r.start, end:r.end, words:r.words})""", {"rows": b})
    g.query("MATCH (f:File), (s:Symbol {path:f.path}) CREATE (f)-[:CONTAINS]->(s)")
    g.query("MATCH (f:File), (t:Test {path:f.path}) CREATE (f)-[:CONTAINS_TEST]->(t)")
    for b in _batches(code.get("imports", [])):
        g.query("""UNWIND $rows AS r MATCH (a:File {path:r.src}), (b:File {path:r.dst})
                   CREATE (a)-[:IMPORTS]->(b)""", {"rows": b})
    for b in _batches(code["calls"]):
        g.query("""UNWIND $rows AS r MATCH (a:Symbol {qname:r.src}), (b:Symbol {qname:r.dst})
                   CREATE (a)-[:CALLS {n:r.n, w:1}]->(b)""", {"rows": b})
        g.query("""UNWIND $rows AS r MATCH (a:Test {id:r.src}), (b:Symbol {qname:r.dst})
                   CREATE (a)-[:STATIC_CALLS {n:r.n}]->(b)""", {"rows": b})
    for b in _batches(covers):
        g.query("""UNWIND $rows AS r MATCH (t:Test {id:r.test}), (s:Symbol {qname:r.sym})
                   CREATE (t)-[:COVERS {lines:r.lines}]->(s)""", {"rows": b})
    commits = [{**c, "ts": _ts(c["at"]), "rev": 1 if c["reverted"] else 0} for c in history["commits"]]
    for b in _batches(commits, 300):
        g.query("""UNWIND $rows AS r
                   MERGE (p:Person {name:r.author})
                   CREATE (c:Commit {sha:r.sha, subject:r.subject, at:r.at, ts:r.ts, reverted:r.rev})
                   CREATE (p)-[:AUTHORED_COMMIT]->(c)
                   WITH c, r UNWIND r.files AS fp
                   MATCH (f:File {path:fp}) CREATE (c)-[:TOUCHED]->(f)""", {"rows": b})
    for b in _batches(history["authored"]):
        g.query("""UNWIND $rows AS r MATCH (p:Person {name:r.person}), (f:File {path:r.file})
                   CREATE (p)-[:WORKED_ON {commits:r.n}]->(f)""", {"rows": b})
    for b in _batches(history["cochange"]):
        g.query("""UNWIND $rows AS r MATCH (a:File {path:r.a}), (b:File {path:r.b})
                   CREATE (a)-[:CO_CHANGED {n:r.n}]->(b)""", {"rows": b})
    if history["cochange"]:
        # strong co-change = at least 35% of the strongest pair (floor 5 commits): WCC over these finds files that really move together
        thr = max(5, round(0.35 * max(r["n"] for r in history["cochange"])))
        for b in _batches([r for r in history["cochange"] if r["n"] >= thr]):
            g.query("""UNWIND $rows AS r MATCH (a:File {path:r.a}), (b:File {path:r.b})
                       CREATE (a)-[:CO_STRONG {n:r.n}]->(b)""", {"rows": b})
    g.query("MATCH (t:Test) SET t.breadth = 0")
    g.query("MATCH (t:Test)-[c:COVERS]->() WITH t, count(c) AS b SET t.breadth = b")
    # algorithm results are persisted on the nodes so risk queries can read them cheaply
    g.query("CALL algo.pageRank('Symbol','CALLS') YIELD node, score SET node.pr = score")
    return g


def stats(g) -> dict:
    out = {}
    for label, q in [("symbols", "MATCH (n:Symbol) RETURN count(n)"), ("tests", "MATCH (n:Test) RETURN count(n)"),
                     ("files", "MATCH (n:File) RETURN count(n)"), ("commits", "MATCH (n:Commit) RETURN count(n)"),
                     ("calls", "MATCH ()-[r:CALLS]->() RETURN count(r)"), ("imports", "MATCH ()-[r:IMPORTS]->() RETURN count(r)"), ("covers", "MATCH ()-[r:COVERS]->() RETURN count(r)"),
                     ("co_changed", "MATCH ()-[r:CO_CHANGED]->() RETURN count(r)")]:
        out[label] = g.ro_query(q).result_set[0][0]
    return out


def read_query(name: str, **sub) -> str:
    """Load queries/<name>.cypher. Only integer placeholders ({{DEPTH}}) are substituted, after validation."""
    text = (QUERIES / f"{name}.cypher").read_text()
    for k, v in sub.items():
        text = text.replace("{{" + k + "}}", str(int(v)))
    return text

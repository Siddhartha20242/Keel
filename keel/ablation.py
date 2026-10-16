"""Ablation: take FalkorDB out and do the same decisions with plain Python dicts, on the same extracted data.

    python -m keel.ablation            # equivalence + latency on the 12 Flask cases, then a synthetic scale test

`MemGraph` mirrors the semantics of queries/impacted_tests, blast_radius, import_radius, risk_* and the PageRank/WCC algorithms.
The point is to measure what the database buys: same answers? faster? slower? how much code?
"""
from __future__ import annotations
import json, pathlib, random, statistics, sys, time
from collections import defaultdict, deque

from . import risk


class MemGraph:
    def __init__(self, code: dict, hist: dict, covers: list[dict], ref_ts: int):
        self.ref_ts = ref_ts
        syms = [s for s in code["symbols"] if s["kind"] != "test"]
        self.kind = {s["qname"]: s["kind"] for s in syms}
        self.path = {s["qname"]: s["path"] for s in syms}
        self.callers: dict[str, set[str]] = defaultdict(set)      # dst -> srcs
        out: dict[str, set[str]] = defaultdict(set)
        for c in code["calls"]:
            if c["src"] in self.kind and c["dst"] in self.kind:
                self.callers[c["dst"]].add(c["src"]); out[c["src"]].add(c["dst"])
        self.out = out
        self.tests_of: dict[str, set[str]] = defaultdict(set)     # symbol -> tests covering it
        self.test_file = {s["qname"]: s["path"] for s in code["symbols"] if s["kind"] == "test"}
        for e in covers:
            if e["sym"] in self.kind and e["test"] in self.test_file:
                self.tests_of[e["sym"]].add(e["test"])
        self.src_files = {f["path"] for f in code["files"] if f["kind"] == "src"}
        self.importers: dict[str, set[str]] = defaultdict(set)    # file -> files importing it
        for i in code.get("imports", []):
            self.importers[i["dst"]].add(i["src"])
        self.commits_of: dict[str, list[dict]] = defaultdict(list)
        for c in hist["commits"]:
            for f in set(c["files"]):
                self.commits_of[f].append(c)
        self.n_tests = len(self.test_file)
        self.pr = self.pagerank()

    # --- traversals (BFS over dicts) -----------------------------------
    def callers_within(self, q: str, depth: int) -> dict[str, int]:
        """caller -> min hops, over 1..depth hops of CALLS ending at q."""
        seen, frontier, hops = {}, {q}, 0
        while frontier and hops < depth:
            hops += 1
            nxt = set()
            for n in frontier:
                for c in self.callers.get(n, ()):
                    if c not in seen and c != q:
                        seen[c] = hops; nxt.add(c)
            frontier = nxt
        return seen

    def blast_radius(self, q: str, depth: int) -> dict[str, int]:
        return {c: h for c, h in self.callers_within(q, depth).items() if self.kind.get(c) == "code"}

    def impacted_tests(self, qnames: list[str], depth: int) -> dict[str, int]:
        best: dict[str, int] = {}
        for q in qnames:
            for t in self.tests_of.get(q, ()):
                best[t] = 0
            for c, h in self.callers_within(q, depth).items():
                for t in self.tests_of.get(c, ()):
                    best[t] = min(best.get(t, 99), h)
        return best

    def import_radius(self, path: str, depth: int = 4) -> int:
        seen, frontier = set(), {path}
        for _ in range(depth):
            nxt = set()
            for f in frontier:
                for x in self.importers.get(f, ()):
                    if x not in seen:
                        seen.add(x); nxt.add(x)
            frontier = nxt
        return len({x for x in seen if x in self.src_files})

    # --- algorithms ----------------------------------------------------
    def pagerank(self, d: float = 0.85, iters: int = 100, tol: float = 1e-9) -> dict[str, float]:
        nodes = list(self.kind)
        n = len(nodes)
        pr = {x: 1.0 / n for x in nodes}
        for _ in range(iters):
            dangling = sum(pr[x] for x in nodes if not self.out.get(x))
            new = {x: (1 - d) / n + d * dangling / n for x in nodes}
            for x in nodes:
                outs = self.out.get(x)
                if outs:
                    share = d * pr[x] / len(outs)
                    for y in outs:
                        new[y] += share
            diff = sum(abs(new[x] - pr[x]) for x in nodes)
            pr = new
            if diff < tol:
                break
        return pr

    def risk_for(self, qnames: list[str]) -> dict:
        code_pr = sorted(v for q, v in self.pr.items() if self.kind[q] == "code")
        import bisect
        sym_rows = []
        for q in qnames:
            below = bisect.bisect_left(code_pr, self.pr[q])            # strict: symbols with pr < this one
            callers = len({c for c in self.callers_within(q, 3) if self.kind.get(c) == "code"})
            sym_rows.append({"symbol": q, "symbols_below": below, "callers": callers, "tests": len(self.tests_of.get(q, ()))})
        paths = sorted({self.path[q] for q in qnames})
        file_rows = []
        for p in paths:
            cs = {c["sha"]: c for c in self.commits_of.get(p, [])}.values()
            file_rows.append({"file": p, "commits": len(cs), "commits_last_year": sum(1 for c in cs if _ts(c["at"]) > self.ref_ts - 31536000),
                              "reverted": sum(1 for c in cs if c["reverted"])})
        imp = [{"file": p, "importers": self.import_radius(p)} for p in paths]
        comp = risk.components(sym_rows, file_rows, len(code_pr), imp, len(self.src_files))
        return {"score": risk.score(comp), "components": comp}


def _ts(day: str) -> int:
    import datetime as dt
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def wcc(nodes: list, edges: list[tuple]) -> int:
    parent = {n: n for n in nodes}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(n) for n in nodes})


# ------------------------------------------------------------------ real cases
def real_cases(db) -> dict:
    from . import agent as A, graph as G, pipeline as P
    from .evaluate import changed_symbols
    from .tools import Toolbox
    rows, t_load, t_mem = [], [], []
    for f in sorted((A.ROOT / "data" / "cases").glob("*.json")):
        c = json.loads(f.read_text())
        wd = P.checkout(c["id"], c["parent"])
        code, hist, covers, meta = P.extract_all(P.REPO_DIR, wd, c["parent"])
        t = time.perf_counter(); G.load_code_graph(db, G.code_graph_name("flask", c["id"]), code, hist, covers, {"repo": "flask", **meta}); t_load.append(time.perf_counter() - t)
        t = time.perf_counter(); mg = MemGraph(code, hist, covers, meta["ref_ts"]); t_mem.append(time.perf_counter() - t)
        edited = [q for q in changed_symbols(c, code) if q in mg.kind]
        if not edited:
            continue
        tb = Toolbox(db, "flask", c["id"], wd)
        t = time.perf_counter(); it = tb.impacted_tests(edited, depth=2, limit=100000); br = {q: tb.blast_radius(q, 3) for q in edited}; rk = tb.risk_score(edited); t_g = time.perf_counter() - t
        t = time.perf_counter(); m_it = mg.impacted_tests(edited, 2); m_br = {q: mg.blast_radius(q, 3) for q in edited}; m_rk = mg.risk_for(edited); t_m = time.perf_counter() - t
        g_tests = {x["test"]: x["hops"] for x in it.get("tests", [])}
        g_blast = {q: {x["symbol"]: x["hops"] for x in br[q].get("callers", [])} for q in edited}
        rows.append({"case": c["id"], "tests_equal": g_tests == m_it, "tests": len(g_tests),
                     "blast_equal": all(g_blast[q] == m_br[q] for q in edited),
                     "score_graph": rk["score"], "score_mem": round(m_rk["score"], 3), "lane_equal": risk.lane(rk["score"])[0] == risk.lane(m_rk["score"])[0],
                     "ms_graph": round(t_g * 1000), "ms_mem": round(t_m * 1000, 1)})
    return {"cases": rows, "load_ms_graph": round(statistics.mean(t_load) * 1000), "build_ms_mem": round(statistics.mean(t_mem) * 1000)}


# ------------------------------------------------------------------ synthetic scale
def synthetic(db, sizes=(1_000, 10_000, 100_000), degree=4, probes=30, seed=7) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for n in sizes:
        edges = [(rnd.randrange(n), rnd.randrange(n)) for _ in range(n * degree)]
        res = {"nodes": n, "edges": len(edges)}
        # in-memory
        t = time.perf_counter()
        callers = defaultdict(set); outs = defaultdict(set)
        for a, b in edges:
            callers[b].add(a); outs[a].add(b)
        res["mem_build_ms"] = round((time.perf_counter() - t) * 1000)
        ids = [rnd.randrange(n) for _ in range(probes)]
        def callers3(x):
            seen, fr = set(), {x}
            for _ in range(3):
                nxt = set()
                for y in fr:
                    for c in callers.get(y, ()):
                        if c not in seen and c != x:
                            seen.add(c); nxt.add(c)
                fr = nxt
            return len(seen)
        t = time.perf_counter(); mem_counts = [callers3(x) for x in ids]; res["mem_3hop_ms"] = round((time.perf_counter() - t) / probes * 1000, 3)
        t = time.perf_counter()
        pr = {i: 1.0 / n for i in range(n)}
        for _ in range(20):
            new = {i: 0.15 / n for i in range(n)}
            for i in range(n):
                o = outs.get(i)
                if o:
                    s = 0.85 * pr[i] / len(o)
                    for j in o:
                        new[j] += s
            pr = new
        res["mem_pagerank_ms"] = round((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); wcc(list(range(n)), edges); res["mem_wcc_ms"] = round((time.perf_counter() - t) * 1000)
        # FalkorDB
        name = f"keel_ablation_{n}"
        try:
            db.select_graph(name).delete()
        except Exception:
            pass
        g = db.select_graph(name)
        t = time.perf_counter()
        g.query("CREATE INDEX FOR (s:S) ON (s.id)")
        for i in range(0, n, 20000):
            g.query("UNWIND range($a, $b) AS i CREATE (:S {id: i})", {"a": i, "b": min(i + 20000, n) - 1})
        for i in range(0, len(edges), 20000):
            # R is the reverse edge: FalkorDB's BFS follows outgoing edges, so "who calls X" needs the reversed relationship
            g.query("UNWIND $es AS e MATCH (a:S {id: e[0]}), (b:S {id: e[1]}) CREATE (a)-[:E]->(b), (b)-[:R]->(a)", {"es": [list(e) for e in edges[i:i + 20000]]})
        res["graph_load_ms"] = round((time.perf_counter() - t) * 1000)
        bfs = "MATCH (s:S {id: $id}) CALL algo.BFS(s, 3, 'R') YIELD nodes, edges RETURN size(nodes)"
        t = time.perf_counter(); bfs_counts = []
        for x in ids:
            r = g.ro_query(bfs, {"id": x}).result_set
            bfs_counts.append(r[0][0] if r else 0)
        res["graph_3hop_bfs_ms"] = round((time.perf_counter() - t) / probes * 1000, 3)
        res["bfs_equal"] = bfs_counts == mem_counts
        if n <= 30_000:     # the all-paths pattern our queries use: far too slow to probe at the largest size
            q = "MATCH (c:S)-[:E*1..3]->(s:S {id: $id}) WHERE c.id <> $id RETURN count(DISTINCT c)"
            t = time.perf_counter(); g_counts = [g.ro_query(q, {"id": x}).result_set[0][0] for x in ids]; res["graph_3hop_pattern_ms"] = round((time.perf_counter() - t) / probes * 1000, 3)
            res["pattern_equal"] = g_counts == mem_counts
        t = time.perf_counter(); g.ro_query("CALL algo.pageRank('S','E') YIELD node, score RETURN count(node)"); res["graph_pagerank_ms"] = round((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); g.ro_query("CALL algo.WCC({nodeLabels:['S'], relationshipTypes:['E']}) YIELD node, componentId RETURN count(DISTINCT componentId)"); res["graph_wcc_ms"] = round((time.perf_counter() - t) * 1000)
        g.delete()
        out.append(res)
        print(res, flush=True)
    return out


def main():
    from . import graph as G
    db = G.connect()
    real = real_cases(db)
    print(json.dumps({k: v for k, v in real.items() if k != "cases"}))
    for r in real["cases"]:
        print(r)
    sizes = tuple(int(x) for x in sys.argv[1:]) or (1_000, 10_000, 100_000)
    syn = synthetic(db, sizes)
    out = pathlib.Path(__file__).resolve().parents[1] / "data" / "ablation.json"
    out.write_text(json.dumps({"real": real, "synthetic": syn}, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()

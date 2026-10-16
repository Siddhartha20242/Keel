"""The agent's tool catalogue. Humans wrote every Cypher query (queries/*.cypher); the model only picks a tool and fills
validated arguments. Names are resolved against the graph before anything runs, so no model string is ever concatenated
into a query."""
from __future__ import annotations
import datetime as dt, json, math, pathlib, re, subprocess, sys, time
from collections import defaultdict
from . import graph as G, risk

MAX_DEPTH = 4
DECAY = 0.35   # share of a seed's weight that survives one call-graph hop
STOP = set("""the and for that this with from have has had was were are but not you your can could should would does did get got when then than
there their them they what which while where who why how will its into out all any also just like some such only more most other flask python
error issue bug problem expected actual using use used work works working need needs want please thanks thank version code file files line
return returns call calls called calling run running data value values None True False self name names type types none""".split())


def extract_terms(text: str, cap: int = 60) -> list[str]:
    """Candidate lexical entry points from an issue: identifiers, backticked names, and their snake/camel parts."""
    terms: dict[str, None] = {}
    text = re.sub(r"https?://\S+", " ", text)
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text):
        low = tok.lower()
        if low not in STOP:
            terms[low] = None
        for part in re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", tok).lower().split("_"):
            if len(part) >= 4 and part not in STOP:
                terms[part] = None
    return list(terms)[:cap]


def _rows(res, cols):
    return [dict(zip(cols, r)) for r in res.result_set]


class Toolbox:
    def __init__(self, db, repo: str, case_id: str, workdir: pathlib.Path, now: str | None = None):
        self.db, self.repo, self.case_id, self.workdir = db, repo, case_id, pathlib.Path(workdir)
        self.g = db.select_graph(G.code_graph_name(repo, case_id))
        self.mem = db.select_graph(G.memory_graph_name(repo))
        self.now = now or dt.date.today().isoformat()
        self.trace: list[dict] = []
        self.evidence: dict = {}           # accumulated facts the PR body is built from
        self.edited: dict[str, list[str]] = {}   # path -> qnames touched (resolved from the diff)

    # ------------------------------------------------------------- plumbing
    def _ro(self, graph, name, params=None, cols=(), **sub):
        t = time.time()
        res = graph.ro_query(G.read_query(name, **sub), params or {})
        rows = _rows(res, cols) if cols else res.result_set
        self.trace.append({"tool_query": name, "ms": round((time.time() - t) * 1000), "rows": len(rows)})
        return rows

    def resolve(self, names: list[str]) -> tuple[list[str], list[str]]:
        rows = self._ro(self.g, "resolve_symbols", {"names": names}, ("name", "qnames"))
        ok, bad = [], []
        for r in rows:
            (ok if r["qnames"] else bad).append(r["qnames"][0] if r["qnames"] else r["name"])
        return ok, bad

    def ref_ts(self) -> int:
        return self.g.ro_query("MATCH (m:Meta) RETURN m.ref_ts").result_set[0][0]

    def _symbols(self, names):
        ok, bad = self.resolve(names)
        if bad and not ok:
            return None, {"outcome": "unknown_symbol", "unknown": bad,
                          "note": "No query ran: these names are not in the graph. Use localize to find real symbol names."}
        return ok, ({"unknown": bad} if bad else {})

    # ----------------------------------------------------------------- tools
    def localize(self, issue_text: str = "", terms: list[str] | None = None, k: int = 5) -> dict:
        terms = [t.lower() for t in (terms or [])] or extract_terms(issue_text)
        n_sym = self.g.ro_query("MATCH (s:Symbol) WHERE s.kind IN ['code','class'] RETURN count(s)").result_set[0][0]
        seeds = self._ro(self.g, "seed_symbols", {"terms": terms, "limit": 40, "n": n_sym, "maxdf": max(8, n_sym // 12)},
                         ("symbol", "file", "seed_weight"))
        if not seeds:
            return {"outcome": "not_in_graph", "terms": terms[:15],
                    "note": "Nothing in the graph matches this text, so I cannot name an edit site. This is not evidence the code is fine."}
        top = seeds[0]["seed_weight"]
        sd = [{"qname": s["symbol"], "w": s["seed_weight"] / top} for s in seeds]
        sym = self._ro(self.g, "localize_expand", {"seeds": sd, "limit": 80, "decay": DECAY}, ("symbol", "file", "score", "because", "nearest"), DEPTH=2)
        n_tests = self.g.ro_query("MATCH (t:Test) RETURN count(t)").result_set[0][0]
        trace = self._ro(self.g, "test_trace", {"terms": terms, "limit": 80, "nt": max(n_tests, 2), "maxdf": max(8, n_tests // 12)},
                         ("symbol", "file", "score", "via_tests"))
        # each signal is normalised to its own maximum so neither dominates by scale; equal weight, no tuning
        smax = max((r["score"] for r in sym), default=1.0) or 1.0
        tmax = max((r["score"] for r in trace), default=1.0) or 1.0
        merged: dict[str, dict] = {}
        for r in sym:
            merged[r["symbol"]] = {**r, "score": r["score"] / smax, "via_tests": []}
        for r in trace:
            m = merged.setdefault(r["symbol"], {"symbol": r["symbol"], "file": r["file"], "score": 0.0, "because": [], "nearest": None, "via_tests": []})
            m["score"] += r["score"] / tmax
            m["via_tests"] = r["via_tests"]
        sym = list(merged.values())
        by_file: dict[str, list[dict]] = defaultdict(list)
        for r in sym:
            by_file[r["file"]].append(r)
        fscore = {f: sum(sorted((r["score"] for r in rs), reverse=True)[:3]) for f, rs in by_file.items() if f.startswith("src/")}
        ranked = sorted(fscore, key=fscore.get, reverse=True)
        co = self._ro(self.g, "cochange", {"paths": ranked[:3]}, ("file", "partner", "times"))
        boosted = dict(fscore)
        for c in co:
            boosted[c["partner"]] = boosted.get(c["partner"], 0.0) + 0.25 * fscore[c["file"]] * min(c["times"], 10) / 10
        order = sorted(boosted, key=boosted.get, reverse=True)[:k]
        out = {"outcome": "answered_with_path", "files": [], "terms_used": terms[:15]}
        for f in order:
            syms = sorted(by_file.get(f, []), key=lambda r: -r["score"])[:3]
            out["files"].append({
                "file": f, "score": round(boosted[f], 3),
                "symbols": [{"symbol": s["symbol"], "score": round(s["score"], 3), "hops_from_issue_terms": s["nearest"],
                             "because": s["because"], "via_tests": s["via_tests"]} for s in syms],
                "cochanged_with": [c["file"] for c in co if c["partner"] == f][:2]})
        self.evidence["localize"] = out
        return out

    def blast_radius(self, symbol: str, depth: int = 3) -> dict:
        depth = max(1, min(int(depth), MAX_DEPTH))
        qn, err = self._symbols([symbol])
        if qn is None:
            return err
        rows = self._ro(self.g, "blast_radius", {"qname": qn[0]}, ("symbol", "file", "hops", "via"), DEPTH=depth)
        if not rows:
            return {"outcome": "not_in_graph", "symbol": qn[0], "depth": depth,
                    "note": "The symbol exists but nothing in the graph calls it within that depth. That can mean a dependency is not modelled (dynamic dispatch), not that nothing depends on it."}
        out = {"outcome": "answered_with_path", "symbol": qn[0], "depth": depth, "callers": rows[:40], "total": len(rows)}
        self.evidence.setdefault("blast", {})[qn[0]] = out
        return out

    def impacted_tests(self, symbols: list[str], depth: int = 2, limit: int = 60) -> dict:
        depth = max(1, min(int(depth), 3))
        qn, err = self._symbols(symbols)
        if qn is None:
            return err
        raw = self._ro(self.g, "impacted_tests", {"qnames": qn}, ("test", "file", "hops", "edited", "through", "breadth"), DEPTH=depth)
        best: dict[str, dict] = {}
        for r in raw:
            cur = best.get(r["test"])
            if cur is None or r["hops"] < cur["hops"]:
                best[r["test"]] = r
        rows = sorted(best.values(), key=lambda r: (r["hops"], r["breadth"], r["test"]))
        total = self.g.ro_query("MATCH (t:Test) RETURN count(t)").result_set[0][0]
        out = {"outcome": "answered_with_path" if rows else "not_in_graph", "symbols": qn, "selected": len(rows), "suite": total,
               "tests": rows[:limit], "files": sorted({r["file"] for r in rows}), "files_in_budget": sorted({r["file"] for r in rows[:limit]})}
        if not rows:
            out["note"] = "No recorded test executes these symbols or their callers."
        self.evidence["tests"] = out
        return out

    def risk_score(self, symbols: list[str]) -> dict:
        qn, err = self._symbols(symbols)
        if qn is None:
            return err
        sym = {r["symbol"]: r for r in self._ro(self.g, "risk_symbols", {"qnames": qn}, ("symbol", "pagerank", "symbols_below"))}
        for r in self._ro(self.g, "risk_callers", {"qnames": qn}, ("symbol", "callers")):
            sym[r["symbol"]]["callers"] = r["callers"]
        for r in self._ro(self.g, "risk_tests", {"qnames": qn}, ("symbol", "tests")):
            sym[r["symbol"]]["tests"] = r["tests"]
        sym = list(sym.values())
        paths = sorted({self.g.ro_query("MATCH (s:Symbol {qname:$q}) RETURN s.path", {"q": q}).result_set[0][0] for q in qn})
        files = self._ro(self.g, "risk_files", {"paths": paths, "now": self.ref_ts()}, ("file", "commits", "commits_last_year", "reverted", "last_touched"))
        total = self.g.ro_query("MATCH (s:Symbol {kind:'code'}) RETURN count(s)").result_set[0][0]
        imp = self._ro(self.g, "import_radius", {"paths": paths}, ("file", "importers"))
        total_src = self.g.ro_query("MATCH (f:File {kind:'src'}) RETURN count(f)").result_set[0][0]
        comp = risk.components(sym, files, total, imp, total_src)
        s = risk.score(comp)
        out = {"outcome": "answered_with_path", "score": round(s, 3), "components": {k: round(v, 3) for k, v in comp.items()},
               "weights": risk.WEIGHTS, "symbols": sym, "files": files, "importers": imp,
               "lane_before_tests": risk.lane(s)[0]}
        self.evidence["risk"] = out
        return out

    def reviewers(self, symbols: list[str], limit: int = 3) -> dict:
        qn, err = self._symbols(symbols)
        if qn is None:
            return err
        paths = sorted({self.g.ro_query("MATCH (s:Symbol {qname:$q}) RETURN s.path", {"q": q}).result_set[0][0] for q in qn})
        # widen to callers' files (the blast radius) so the people who own the *consumers* review too
        rad = self.g.ro_query("""UNWIND $q AS x MATCH (c:Symbol {kind:'code'})-[:CALLS*1..2]->(s:Symbol {qname:x}) RETURN DISTINCT c.path""", {"q": qn}).result_set
        paths = sorted(set(paths) | {r[0] for r in rad})
        rows = self._ro(self.g, "reviewers", {"paths": paths, "limit": max(1, min(int(limit), 10)), "now": self.ref_ts()}, ("person", "commits", "recent", "files", "last"))
        out = {"outcome": "answered_with_path" if rows else "not_in_graph", "paths_considered": paths, "reviewers": rows}
        self.evidence["reviewers"] = out
        return out

    def path_between(self, source: str, target: str) -> dict:
        qn, err = self._symbols([source, target])
        if qn is None:
            return err
        if len(qn) < 2:
            return {"outcome": "unknown_symbol", "unknown": err.get("unknown", [])}
        rows = self._ro(self.g, "path_between", {"from": qn[0], "to": qn[1]}, ("hops", "weight"))
        if not rows:
            return {"outcome": "not_in_graph", "from": qn[0], "to": qn[1],
                    "note": "No call route from source to target in the graph (static analysis; dynamic dispatch is not modelled)."}
        return {"outcome": "answered_with_path", "hops": rows[0]["hops"], "length": rows[0]["weight"]}

    def load_bearing(self, limit: int = 10) -> dict:
        rows = self._ro(self.g, "load_bearing", {"limit": max(1, min(int(limit), 20))}, ("symbol", "file", "score"))
        return {"outcome": "answered_with_path", "symbols": [{**r, "score": round(r["score"], 4)} for r in rows]}

    def changes_with(self, files: list[str], limit: int = 6) -> dict:
        """Files that strongly tend to change together with `files` (weakly connected components of CO_STRONG), minus `files`."""
        comps = self._ro(self.g, "clusters", {}, ("component", "files", "size"))
        mine = set(files)
        partners: list[str] = []
        for c in comps:
            if c["size"] > 1 and mine & set(c["files"]):
                partners += [f for f in c["files"] if f not in mine and f not in partners]
        if not partners:
            return {"outcome": "not_in_graph", "note": "No strong co-change cluster contains these files."}
        return {"outcome": "answered_with_path", "partners": partners[:limit]}

    def list_symbols(self, file: str = "", limit: int = 40) -> dict:
        rows = self._ro(self.g, "list_symbols", {"file": file or "", "limit": max(1, min(int(limit), 80))}, ("symbol", "file"))
        if not rows:
            return {"outcome": "not_in_graph", "note": "No code symbols" + (f" in {file}" if file else "") + "."}
        return {"outcome": "answered_with_path", "symbols": rows}

    def recall(self, symbols: list[str], limit: int = 5) -> dict:
        qn, err = self._symbols(symbols)
        if qn is None:
            return err
        try:
            rows = self._ro(self.mem, "mem_recall", {"qnames": qn, "limit": limit}, ("id", "issue", "summary", "outcome", "lane", "at"))
        except Exception:
            rows = []
        if not rows:
            return {"outcome": "not_in_graph", "note": "No earlier attempt touched these symbols."}
        out = {"outcome": "answered_from_memory", "attempts": rows}
        self.evidence["recall"] = out
        return out

    def remember(self, issue: str, summary: str, outcome: str, lane: str, symbols: list[str]) -> dict:
        qn, _ = self.resolve(symbols)
        aid = f"{self.case_id}-{int(time.time())}"
        rec = {"id": aid, "title": issue, "summary": summary[:500], "outcome": outcome, "lane": lane, "at": self.now, "symbols": qn}
        self.mem.query(G.read_query("mem_remember"), {"id": aid, "issue": issue, "summary": summary[:500], "outcome": outcome,
                                                     "lane": lane, "at": self.now, "qnames": qn or ["<none>"], "json": json.dumps(rec, sort_keys=True)})
        return {"outcome": "stored", "id": aid, "symbols": qn}

    # ------------------------------------------------------------- actions
    def read_symbol(self, symbol: str) -> dict:
        qn, err = self._symbols([symbol])
        if qn is None:
            return err
        r = self.g.ro_query("MATCH (s:Symbol {qname:$q}) RETURN s.path, s.start, s.end", {"q": qn[0]}).result_set[0]
        lines = (self.workdir / r[0]).read_text().splitlines()[r[1] - 1:r[2]]
        return {"outcome": "answered_with_path", "symbol": qn[0], "file": r[0], "start": r[1], "source": "\n".join(lines)[:6000]}

    def _file_known(self, path: str) -> bool:
        return bool(self.g.ro_query("MATCH (f:File {path:$p}) RETURN f", {"p": path}).result_set)

    def edit_file(self, path: str, old: str, new: str) -> dict:
        if not self._file_known(path):
            return {"outcome": "unknown_symbol", "unknown": [path], "note": "File is not in the graph; edits are limited to known files."}
        p = self.workdir / path
        text = p.read_text()
        if text.count(old) != 1:
            return {"outcome": "error", "note": f"`old` must match exactly once (matched {text.count(old)})."}
        p.write_text(text.replace(old, new))
        # which symbols did this edit land in? Map the changed lines through the graph.
        line = text[:text.index(old)].count("\n") + 1
        rows = self.g.ro_query("""MATCH (s:Symbol {path:$p}) WHERE s.start <= $l AND s.end >= $l AND s.kind = 'code'
                                  RETURN s.qname ORDER BY s.end - s.start LIMIT 1""", {"p": path, "l": line}).result_set
        q = rows[0][0] if rows else None
        if q:
            self.edited.setdefault(path, [])
            if q not in self.edited[path]:
                self.edited[path].append(q)
        return {"outcome": "answered_with_path", "edited_symbol": q, "file": path}

    @staticmethod
    def _test_id(t: str) -> str:
        """Models mangle ids: accept tests.mod.test_x, tests/mod.py::test_x, tests/mod.test_x and Class variants."""
        t = t.strip().replace("::", ".").replace("/", ".")
        t = t.replace(".py.", ".")
        return t[:-3] if t.endswith(".py") else t

    def run_nodeids(self, ids: list[str], timeout: int = 300) -> dict:
        """Run exact pytest node ids (tests/x.py::name). Only well-formed ids are run; used to check that a fix makes the failing tests pass."""
        import os
        ok = [i for i in ids if re.match(r"^tests/[\w./-]+\.py::[\w:.\[\]-]+$", i)]
        if not ok:
            return {"passed": False, "failed": [], "ran": 0}
        env = dict(os.environ, PYTHONPATH=str(self.workdir / "src"))
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", *ok],
                           cwd=self.workdir, env=env, capture_output=True, text=True, timeout=timeout)
        return {"passed": p.returncode == 0, "failed": sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", p.stdout, re.M))), "ran": len(ok)}

    def run_tests(self, tests: list[str], timeout: int = 300, extra_files: list[str] | None = None) -> dict:
        ids = []
        for t in tests[:300]:
            t = self._test_id(t)
            row = self.g.ro_query("MATCH (x:Test {id:$t}) RETURN x.path", {"t": t}).result_set
            if not row:
                continue
            rest = t.split(".", 2)[2] if t.count(".") >= 2 else t
            ids.append(f"{row[0][0]}::" + "::".join(rest.split(".")))
        for f in extra_files or []:                       # test files the change itself touched
            if self._file_known(f) or (self.workdir / f).exists():
                ids.append(f)
        if not ids:
            return {"outcome": "unknown_symbol", "unknown": tests[:5], "note": "None of these are tests in the graph."}
        import os
        env = dict(os.environ, PYTHONPATH=str(self.workdir / "src"))
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
        p = subprocess.run([*cmd, *ids], cwd=self.workdir, env=env, capture_output=True, text=True, timeout=timeout)
        missing = []
        if p.returncode == 4 and "not found" in (p.stdout + p.stderr):
            # the change itself renamed/removed some selected tests; run the ones that still exist and say so
            missing = [m.replace(str(self.workdir) + "/", "") for m in re.findall(r"not found: (\S+)", p.stdout + p.stderr)]
            ids = [i for i in ids if i not in missing]
            p = subprocess.run([*cmd, *ids], cwd=self.workdir, env=env, capture_output=True, text=True, timeout=timeout)
        tail = "\n".join(p.stdout.strip().splitlines()[-12:])
        failed = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", p.stdout, re.M)))
        out = {"outcome": "answered_with_path", "ran": len(ids), "passed": p.returncode == 0, "failed": failed, "returncode": p.returncode, "missing": missing, "tail": tail}
        self.evidence["run"] = out
        return out

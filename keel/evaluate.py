"""Replay real issues at the parent of the real fix; score graph decisions against what the real fix changed."""
from __future__ import annotations
import json, pathlib, re, subprocess, sys
from . import graph as G, pipeline as P
from .baseline import localize_no_graph, tests_same_name
from .tools import Toolbox
from .extract import extract_code

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUDGET = 40   # tests the agent runs first


def issue_text(c):
    return c["title"] + "\n" + c["body"][:2500]


def changed_symbols(case, code):
    """Symbols (in the parent checkout) whose lines the real fix touched, from the diff's old-side hunks."""
    out = []
    for f in case["truth_src"]:
        diff = subprocess.run(["git", "show", case["fix_commit"], "-U0", "--format=", "--", f], cwd=P.REPO_DIR, capture_output=True, text=True).stdout
        ranges = []
        for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M):
            a = int(m.group(1)); n = int(m.group(2) or 1)
            ranges.append((a, a + max(n, 1) - 1))
        syms = sorted((s for s in code["symbols"] if s["path"] == f and s["kind"] == "code"), key=lambda s: s["end"] - s["start"])
        for a, b in ranges:
            for line in range(a, b + 1):
                s = next((s for s in syms if s["start"] <= line <= s["end"]), None)
                if s and s["qname"] not in out:
                    out.append(s["qname"])
    return out


def run(case_ids=None, rebuild=False, split=None):
    db = G.connect()
    rows = []
    for f in sorted((ROOT / "data" / "cases").glob("*.json")):
        c = json.loads(f.read_text())
        if (case_ids and c["id"] not in case_ids) or (split and c.get("split") != split):
            continue
        name = G.code_graph_name("flask", c["id"])
        if rebuild or name not in db.list_graphs():
            P.build(db, c["id"], c["parent"])
        wd = P.checkout(c["id"], c["parent"])
        tb = Toolbox(db, "flask", c["id"], wd)
        text = issue_text(c)
        loc = tb.localize(text, k=5)
        graph_files = [x["file"] for x in loc.get("files", [])]
        base_files = localize_no_graph(wd, text)
        code = extract_code(wd)
        edited = changed_symbols(c, code)
        it = tb.impacted_tests(edited, depth=2, limit=BUDGET) if edited else {"files": [], "selected": 0, "suite": 0}
        all_tests = sorted({t["path"] for t in code["symbols"] if t["kind"] == "test"})
        naive = tests_same_name(c["truth_src"], all_tests)
        truth_t = set(c["truth_tests"])
        def rank(lst):
            for i, x in enumerate(lst):
                if x in c["truth_src"]:
                    return i + 1
            return None
        rows.append({
            "case": c["id"], "issue": c["issue_number"], "title": c["title"][:60], "truth": c["truth_src"],
            "graph_rank": rank(graph_files), "baseline_rank": rank(base_files),
            "graph_files": graph_files[:3], "baseline_files": base_files[:3],
            "edited_symbols": edited,
            "test_recall_graph": (len(truth_t & set(it["files"])) / len(truth_t)) if truth_t else None,
            "test_recall_budget": (len(truth_t & set(it.get("files_in_budget", []))) / len(truth_t)) if truth_t else None,
            "test_recall_samename": (len(truth_t & set(naive)) / len(truth_t)) if truth_t else None,
            "tests_selected": it["selected"], "suite": it["suite"],
        })
    return rows


def summarize(rows):
    n = len(rows)
    def hit(key, k):
        return sum(1 for r in rows if r[key] and r[key] <= k)
    lines = [f"cases: {n}", "localization (does the real fix's file appear in the top-k?)",
             f"  graph     top1 {hit('graph_rank',1)}/{n}  top3 {hit('graph_rank',3)}/{n}  top5 {hit('graph_rank',5)}/{n}",
             f"  no-graph  top1 {hit('baseline_rank',1)}/{n}  top3 {hit('baseline_rank',3)}/{n}  top5 {hit('baseline_rank',5)}/{n}"]
    tr = [r for r in rows if r["test_recall_graph"] is not None]
    if tr:
        g = sum(r["test_recall_graph"] for r in tr) / len(tr); s = sum(r["test_recall_samename"] for r in tr) / len(tr)
        sel = sum(r["tests_selected"] for r in tr) / len(tr); suite = sum(r["suite"] for r in tr) / len(tr)
        lines += ["test selection (share of the real fix's test files that the selection reaches)",
                  f"  graph, everything selected  {g:.0%}   avg {sel:.0f} of {suite:.0f} tests",
                  f"  graph, first {BUDGET} tests only  {sum(r['test_recall_budget'] for r in tr)/len(tr):.0%}",
                  f"  same-name heuristic       {s:.0%}"]
    return "\n".join(lines)


if __name__ == "__main__":
    rows = run(rebuild="--rebuild" in sys.argv, split=next((a.split("=")[1] for a in sys.argv if a.startswith("--split=")), None))
    for r in rows:
        print(json.dumps(r))
    print(summarize(rows))
    (ROOT / "data" / "eval_results.json").write_text(json.dumps(rows, indent=1))

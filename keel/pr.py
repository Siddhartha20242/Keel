"""PR body assembled from graph results. Every claim in it is a row or a path that came back from a query."""
from __future__ import annotations
import re

BACKTICK = re.compile(r"`([A-Za-z_][\w.]*)`")


def accepts(sentence: str, known_names: set[str], known_paths: set[str]) -> tuple[bool, str]:
    """Reject a model-written summary that names code that is not in the graph. Backticked names must resolve."""
    short = {n.rsplit(".", 1)[-1] for n in known_names}
    invented = [t for t in BACKTICK.findall(sentence)
                if t not in known_names and t not in short and t not in known_paths and t.split("/")[-1] not in {p.split("/")[-1] for p in known_paths}]
    if invented:
        return False, f"names not in the graph: {sorted(set(invented))}"
    return True, "ok"


def _short(q: str) -> str:
    return q.replace("flask.", "", 1)


def build_body(issue: dict, ev: dict, lane: str, lane_reasons: list[str], edited: dict[str, list[str]], summary: str) -> str:
    L = []
    L.append(f"Fixes #{issue['number']}: {issue['title']}\n")
    L.append(summary.strip() + "\n")
    L.append(f"## Merge lane: **{lane.replace('_', ' ')}**")
    r = ev.get("risk")
    if r:
        L.append(f"Risk score **{r['score']}** (weights: " + ", ".join(f"{k} {v}" for k, v in r["weights"].items()) + ")\n")
        L.append("| signal | value | what it measures |\n|---|---|---|")
        meaning = {"centrality": "PageRank percentile of the edited symbols over the call graph",
                   "reach": "transitive callers within 3 hops (log scale)",
                   "import_reach": "share of other source files that import the edited files (4 levels)",
                   "coverage_gap": "share of edited symbols that no test executes",
                   "churn": "commits touching these files in the last year",
                   "reverts": "earlier changes to these files that were reverted"}
        for k, v in r["components"].items():
            L.append(f"| {k} | {v} | {meaning[k]} |")
        L.append("")
    for why in lane_reasons:
        L.append(f"- Lane override: {why}")
    L.append("\n## What changed")
    for path, syms in edited.items():
        L.append(f"- `{path}`: " + ", ".join(f"`{_short(s)}`" for s in syms))
    loc = ev.get("localize")
    if loc and loc.get("files"):
        L.append("\n## Why here (localization)")
        for f in loc["files"][:3]:
            top = f["symbols"][0] if f["symbols"] else None
            via = f" via tests {', '.join(t.split('.')[-1] for t in top['via_tests'][:2])}" if top and top.get("via_tests") else ""
            L.append(f"- `{f['file']}` score {f['score']}" + (f", best symbol `{_short(top['symbol'])}`{via}" if top else ""))
    blast = ev.get("blast") or {}
    for sym, b in blast.items():
        if b.get("callers"):
            L.append(f"\n## Blast radius of `{_short(sym)}` ({b['total']} callers within {b['depth']} hops)")
            for c in b["callers"][:6]:
                L.append(f"- {' → '.join(_short(x) for x in c['via'])}")
    t = ev.get("tests")
    if t:
        L.append(f"\n## Tests: graph selected {t['selected']} of {t['suite']}")
        for x in t["tests"][:6]:
            how = "executed it directly" if x["hops"] == 0 else f"executes `{_short(x['through'])}`, {x['hops']} hop(s) upstream"
            L.append(f"- `{x['test'].replace('tests.', '', 1)}`: {how}")
    run = ev.get("run")
    if run:
        pre = len(run["failed"]) - len(run.get("new_failures", []))
        L.append(f"\nRan {run['ran']} tests: **{'passed' if run['passed'] else 'FAILED'}**"
                 + (f" ({', '.join(run['new_failures'][:3])})" if run.get("new_failures") else "")
                 + (f"; {len(run['missing'])} selected test(s) no longer exist after the change (renamed or removed by the PR)" if run.get("missing") else "")
                 + (f"; {pre} failure(s) already present before the patch are ignored" if pre > 0 else ""))
    rv = ev.get("reviewers")
    if rv and rv.get("reviewers"):
        L.append("\n## Suggested reviewers (git history over the blast radius)")
        for p in rv["reviewers"]:
            L.append(f"- {p['person']}: {p['commits']} commits ({p['recent']} in the last 2 years), e.g. {', '.join(p['files'][:2])}")
    rc = ev.get("recall")
    if rc and rc.get("attempts"):
        L.append("\n## Memory: earlier attempts on these symbols")
        for a in rc["attempts"]:
            L.append(f"- {a['at']}: {a['outcome']} ({a['lane']}), issue: {a['issue'][:70]}")
    L.append("\n_Opened by Keel. Every number above is a FalkorDB query result; see queries/*.cypher._")
    return "\n".join(L)

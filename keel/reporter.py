"""The Reporter: writes the status note a human reads before deciding. It quotes the Skeptic's verdict and the gate's decision,
lists the graph's evidence, and says what a person still has to decide. It cannot merge or edit anything.
Everything decision-relevant is built from facts; a model (if available) only adds a short plain-language summary, and that
summary is dropped if it names code that is not in the facts."""
from __future__ import annotations
import argparse, json, os, pathlib, re
from .ghio import upsert_comment
from .llm import LLMUnavailable, complete
from .skeptic import known_names

SUMMARY_SYSTEM = """You write a 2-3 sentence plain-language summary of a code change for the person who will approve it.
State what the change does and the single biggest thing to check. Use only names that appear in the input. No headings, no list."""
TICK = re.compile(r"`([A-Za-z_][\w./]*)`")


def short(q: str) -> str:
    return q.replace("shortener.", "", 1)


def still_to_decide(ctx: dict, decision: dict) -> list[str]:
    t, items = ctx["tests"], []
    if t["new_failures"]:
        items.append(f"Whether to fix or drop the {len(t['new_failures'])} test(s) this change breaks: "
                     + ", ".join(f"`{f.split('::')[-1]}`" for f in t["new_failures"][:4]) + ".")
    if ctx["new_files"] and not ctx["changed_tests"]:
        items.append("Whether to accept new code that no test covers: " + ", ".join(f"`{f}`" for f in ctx["new_files"]) + ".")
    elif ctx["edited"] and not t["selected"]:
        items.append("Whether to accept edits to code that no test executes: " + ", ".join(f"`{short(s)}`" for s in ctx["edited"][:3]) + ".")
    if ctx["changed_tests"]:
        items.append("Whether the changed tests assert the right behaviour; tests that change with the code can hide a regression: "
                     + ", ".join(f"`{p}`" for p in ctx["changed_tests"]) + ".")
    for sym, b in list(ctx["blast"].items())[:2]:
        if b["total"]:
            items.append(f"Whether the change is acceptable to the {b['total']} caller(s) of `{short(sym)}` within 3 hops, e.g. "
                         + " -> ".join(short(x) for x in b["routes"][0]) + ".")
    bad = [a for a in ctx["memory"] if a["outcome"] in ("failed_tests", "rejected", "reverted")]
    if bad:
        items.append(f"Why an earlier attempt on this code ended in `{bad[0]['outcome']}` ({bad[0]['at']}), and whether this PR avoids that.")
    if ctx.get("changes_with"):
        items.append("Whether these files, which usually change together with this PR's files, also need a change: "
                     + ", ".join(f"`{p}`" for p in ctx["changes_with"][:4]) + ".")
    if ctx["reviewers"]:
        items.append("Who reviews: the people who have changed this code are " + ", ".join(r["person"] for r in ctx["reviewers"]) + ".")
    items.append("Whether this ships at all. Nothing in this pipeline merges.")
    return items


def build(ctx: dict, decision: dict, skeptic_body: str | None) -> str:
    t, r = ctx["tests"], ctx["risk"]
    L = ["### Status for the human", "", f"**{decision['decision']}**", ""]
    if True:
        verdict = f"RISK: {decision['skeptic_risk']}" if decision["have_review"] else "no review"
        L += [f"Skeptic: {verdict}. Graph lane: **{r['lane'].replace('_', ' ')}**" + (f" ({'; '.join(r['reasons'])})" if r["reasons"] else "") + ".", ""]
    L += ["| evidence | value |", "|---|---|",
          f"| edited symbols | {', '.join('`'+short(s)+'`' for s in ctx['edited']) or 'none'} |",
          f"| tests selected by the graph | {t['selected']} of {t['suite']} |",
          f"| tests run / new failures | {t['ran']} / {len(t['new_failures'])} |",
          f"| risk score | {r['score']} |"]
    for k, v in r["components"].items():
        L.append(f"| &nbsp;&nbsp;{k} | {v} |")
    L.append("")
    for sym, b in list(ctx["blast"].items())[:2]:
        if b["routes"]:
            L.append(f"Blast radius of `{short(sym)}`: " + "; ".join(" -> ".join(short(x) for x in rt) for rt in b["routes"][:3]))
    led = ctx.get("ledger")
    if led:
        L += ["", f"Audit ledger: {'intact' if led['ok'] else '**BROKEN, do not trust earlier records**'} ({led['msg']})."]
    L += ["", "**What a human still has to decide**", ""] + [f"- {i}" for i in still_to_decide(ctx, decision)]
    return "\n".join(L)


def summary(ctx: dict) -> str | None:
    try:
        text = complete(SUMMARY_SYSTEM, f"{ctx['title']}\n\nDIFF:\n{ctx['diff'][:5000]}", max_tokens=200)
    except (LLMUnavailable, Exception):
        return None
    if {t for t in TICK.findall(text) if t not in known_names(ctx)}:
        return None
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.reporter")
    ap.add_argument("--context", default="ci-out/context.json"); ap.add_argument("--repo"); ap.add_argument("--pr")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cpath = pathlib.Path(a.context)
    ctx = json.loads(cpath.read_text())
    dpath = cpath.with_name("decision.json")
    decision = json.loads(dpath.read_text()) if dpath.exists() else {"decision": "DECISION: gate did not run - human approval required",
                                                                      "level": "human", "skeptic_risk": "unknown", "graph_lane": ctx["risk"]["lane"], "have_review": False}
    spath = cpath.with_name("skeptic.txt")
    body = build(ctx, decision, spath.read_text() if spath.exists() else None)
    s = summary(ctx)
    if s:
        body = body.replace("| evidence | value |", f"{s}\n\n| evidence | value |", 1)
    data = {"id": f"pr{ctx['pr']}", "pr": int(ctx["pr"]) if str(ctx["pr"]).isdigit() else 0, "files": ctx.get("files", []),
            "tests_selected": ctx["tests"]["selected"], "new_failures": ctx["tests"]["new_failures"], "decision": decision["decision"],
            "level": decision["level"], "graph_lane": decision["graph_lane"], "run": os.environ.get("GITHUB_RUN_ID", ""), "title": ctx["title"], "summary": s or ctx["title"], "lane": ctx["risk"]["lane"], "risk": ctx["risk"]["score"],
            "symbols": ctx["edited"], "at": ctx["at"], "skeptic_risk": decision["skeptic_risk"]}
    body += f"\n\n<!-- keel:data {json.dumps(data, sort_keys=True)} -->"
    print(body)
    if not a.dry_run and a.repo and a.pr:
        print("comment", upsert_comment(a.repo, a.pr, "report", body))


if __name__ == "__main__":
    main()

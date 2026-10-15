"""Issue stage: read a new issue, find the part of the code it is about using the graph, and say who owns it, which tests cover
it, what depends on it and whether an agent could plausibly fix it. Posts one comment on the issue and suggests labels.
It cannot assign, close or edit anything; a human decides what happens next.

    python -m keel.triage --repo-dir . --subdir demo-app --sha <main sha> --issue 5 --title "..." --body-file body.txt --out ci-out
"""
from __future__ import annotations
import argparse, json, pathlib, re, shutil, subprocess, tempfile
from . import graph as G, pipeline as P, risk
from .ghio import apply_labels, ensure_label, upsert_comment
from .llm import LLMUnavailable, complete
from .memory_io import load_into_graph, read_records
from .tools import Toolbox

SUMMARY_SYSTEM = """You summarise a bug report for a maintainer in 2 sentences: what the reporter sees and what they expect.
Use only what the report says. No headings, no list, no guesses about the cause."""


def triage(repo_dir, subdir, sha, issue, title, body, out_dir, memory_file=None, repo_name="demoapp", db=None) -> dict:
    repo_dir, out_dir = pathlib.Path(repo_dir).resolve(), pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = db or G.connect()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="keel-triage-")).resolve()
    try:
        wt = tmp / "main"
        subprocess.run(["git", "worktree", "add", "--detach", "-f", str(wt), sha], cwd=repo_dir, check=True, capture_output=True)
        case = f"issue{issue}"
        _, stats = P.build_for(db, repo_name, case, repo_dir, wt, sha, subdir=subdir)
        load_into_graph(db, repo_name, read_records(memory_file))
        day = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", sha], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
        tb = Toolbox(db, repo_name, case, wt / subdir, now=day)

        loc = tb.localize(f"{title}\n{body[:2000]}", k=3)
        if loc.get("outcome") != "answered_with_path":
            ctx = {"issue": issue, "title": title, "outcome": "not_in_graph", "suspects": [], "labels": ["keel:needs-human"],
                   "recommendation": "needs-human", "why": "Nothing in the code graph matches this report, so I cannot name where to look. "
                   "That is not evidence the code is fine.", "tests": {}, "owners": [], "blast": {}, "memory": [], "risk": None}
        else:
            suspects = [f["symbols"][0]["symbol"] for f in loc["files"][:2] if f["symbols"]]     # top symbol per file; the rest is noise
            suspects = list(dict.fromkeys(suspects))
            tests = tb.impacted_tests(suspects, depth=2, limit=10)
            rk = tb.risk_score(suspects)
            recall = tb.recall(suspects)
            owners = tb.reviewers(suspects, limit=3)
            blast = {s: tb.blast_radius(s, depth=3) for s in suspects[:2]}
            bad = [a for a in recall.get("attempts", []) if a["outcome"] in ("failed_tests", "rejected", "reverted")]
            lane, why_l = risk.lane(rk["score"], prior_reverted=bool(bad), untested=not tests.get("selected"))
            fixable = lane in ("auto_merge", "canary") and not bad and bool(tests.get("selected"))
            if fixable:
                why = (f"The suspected code is covered by {tests['selected']} test(s), nothing depends on it in a risky way "
                       f"(graph lane: {lane}) and no earlier attempt failed. An agent can try; a human still reviews the PR.")
            else:
                reasons = list(why_l) or [f"graph lane: {lane}"]
                why = "A person should look first: " + "; ".join(reasons) + "."
            ctx = {"issue": issue, "title": title, "outcome": "answered_with_path",
                   "suspects": [{"file": f["file"], "symbols": [s["symbol"] for s in f["symbols"][:3]]} for f in loc["files"]],
                   "tests": {"selected": tests.get("selected", 0), "suite": tests.get("suite", 0),
                             "first": [t["test"] for t in tests.get("tests", [])[:5]]},
                   "risk": {"score": rk["score"], "lane": lane}, "owners": owners.get("reviewers", [])[:3],
                   "blast": {s: {"total": b.get("total", 0), "routes": [c["via"] for c in b.get("callers", [])[:3]]} for s, b in blast.items()},
                   "memory": recall.get("attempts", [])[:3], "recommendation": "agent-can-try" if fixable else "needs-human",
                   "why": why, "labels": ["keel:agent-can-try" if fixable else "keel:needs-human", f"keel:risk-{lane.replace('_', '-')}"]}
        (out_dir / "triage.json").write_text(json.dumps(ctx, indent=1, default=str))
        return ctx
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp / "main")], cwd=repo_dir, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def sanitize(text: str) -> str:
    """The summary is model output derived from an untrusted issue body: drop links and @-mentions before posting it."""
    text = re.sub(r"https?://\S+", "[link removed]", text)
    text = re.sub(r"@([A-Za-z0-9_-]+)", lambda m: "@\u200b" + m.group(1), text)    # zero-width space: no notification
    return text.strip()[:600]


def short(q: str) -> str:
    return q.replace("shortener.", "", 1)


def render(ctx: dict, summary: str | None = None) -> str:
    L = ["### Triage", ""]
    if summary:
        L += [summary, ""]
    L += [f"**Recommendation: {ctx['recommendation'].replace('-', ' ')}.** {ctx['why']}", ""]
    if ctx["suspects"]:
        L.append("**Where the code graph points**")
        for s in ctx["suspects"]:
            L.append(f"- `{s['file']}`: " + ", ".join(f"`{short(x)}`" for x in s["symbols"]))
        t = ctx["tests"]
        L += ["", f"**Tests that exercise it:** {t['selected']} of {t['suite']}" + (": " + ", ".join(f"`{x.split('.')[-1]}`" for x in t["first"][:4]) if t["first"] else "")]
        for sym, b in ctx["blast"].items():
            if b["routes"]:
                L.append(f"**What depends on `{short(sym)}`:** {b['total']} caller(s), e.g. " + " -> ".join(short(x) for x in b["routes"][0]))
        if ctx["owners"]:
            L.append("**Who has changed this code:** " + ", ".join(o["person"] for o in ctx["owners"]))
        if ctx["memory"]:
            L.append("**Earlier attempts on this code:** " + "; ".join(f"{a['at']} {a['outcome']}" for a in ctx["memory"]))
        L.append(f"**Risk preview:** {ctx['risk']['score']} (lane {ctx['risk']['lane'].replace('_', ' ')})")
    L += ["", "_Suggested labels: " + ", ".join(f"`{x}`" for x in ctx["labels"]) + ". A human decides what happens next; to have the agent try, add the `keel:fix` label._"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.triage")
    ap.add_argument("--repo-dir", default="."); ap.add_argument("--subdir", default="demo-app"); ap.add_argument("--sha", required=True)
    ap.add_argument("--issue", required=True); ap.add_argument("--title", default=""); ap.add_argument("--body-file")
    ap.add_argument("--out", default="ci-out"); ap.add_argument("--memory"); ap.add_argument("--repo"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    body = pathlib.Path(a.body_file).read_text() if a.body_file else ""
    ctx = triage(a.repo_dir, a.subdir, a.sha, a.issue, a.title, body, a.out, a.memory)
    summary = None
    try:
        summary = sanitize(complete(SUMMARY_SYSTEM, f"{a.title}\n\n{body[:3000]}", max_tokens=160))
    except (LLMUnavailable, Exception):
        pass
    text = render(ctx, summary)
    pathlib.Path(a.out, "triage.md").write_text(text)
    print(text)
    if not a.dry_run and a.repo:
        print("comment", upsert_comment(a.repo, a.issue, "triage", text))
        apply_labels(a.repo, a.issue, ctx["labels"])
        ensure_label(a.repo, "keel:fix")   # created so a human can apply it; the pipeline never adds it itself



if __name__ == "__main__":
    main()

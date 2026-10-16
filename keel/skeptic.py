"""The Skeptic: reads the diff plus the graph facts and tries to break the change. It cannot approve, merge or edit; its only
output is one review ending in `RISK: low|medium|high`. If no model is available it says so and gives no verdict."""
from __future__ import annotations
import argparse, json, pathlib, re
from .ghio import upsert_comment
from .llm import LLMUnavailable, complete, have_key

SYSTEM = """You are the Skeptic. Your only job is to find ways a code change could break production. You cannot approve, merge or edit anything.
You get the diff and facts from a code graph: blast radius, import radius, tests, risk, history, earlier attempts.
Write at most 4 findings, one per line, in exactly this form:
1. <one sentence naming the function, test or caller and why it could break> | file: <path from the input, or none> | test: <a snake_case test name that would catch it>
Use only names that appear in the input. If the facts show no real risk, write one line saying so instead of inventing findings.
Finish with exactly one last line: RISK: low, RISK: medium or RISK: high.
high = the change likely breaks behaviour or tests fail. medium = real unknowns: untested code, a central function, many callers or importers,
or an earlier failed attempt. low = small and covered by tests that pass."""

RISK_RE = re.compile(r"^\s*RISK:\s*(low|medium|high)\s*$", re.I | re.M)
TICK = re.compile(r"`([A-Za-z_][\w./]*)`")


def facts(ctx: dict) -> str:
    t = ctx["tests"]
    lines = [
        f"PR: {ctx['title']}",
        f"Edited symbols: {', '.join(ctx['edited']) or 'none'}",
        f"Files touched (use these exact paths in `file:`): {', '.join(ctx.get('files', [])) or 'none'}",
        f"New source files: {', '.join(ctx['new_files']) or 'none'}; tests changed in this PR: {', '.join(ctx['changed_tests']) or 'none'}",
        f"Tests: graph selected {t['selected']} of {t['suite']}; ran {t['ran']}; new failures: {', '.join(t['new_failures']) or 'none'}",
        f"Risk score {ctx['risk']['score']} (lane {ctx['risk']['lane']}); components {json.dumps(ctx['risk']['components'])}",
    ]
    for sym, b in ctx["blast"].items():
        lines.append(f"Callers of {sym} within 3 hops: {b['total']}; routes: " + "; ".join(" -> ".join(r) for r in b["routes"][:4]))
    if ctx["memory"]:
        lines.append("Earlier attempts on these symbols: " + "; ".join(f"{a['at']} {a['outcome']}" for a in ctx["memory"][:3]))
    return "\n".join(lines) + "\n\nDIFF:\n" + ctx["diff"][:6000]


def known_names(ctx: dict) -> set[str]:
    def parts(q):          # a qualified name also means each of its segments: module, class, function
        return {q, *q.split(".")}
    names = set(ctx["new_files"]) | set(ctx["changed_tests"])
    for q in ctx["edited"]:
        names |= parts(q)
    for b in ctx["blast"].values():
        for r in b["routes"]:
            for x in r:
                names |= parts(x)
    names |= {t["test"].rsplit(".", 1)[-1] for t in ctx["tests"]["first"]}
    names |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_./]+", ctx["diff"]))
    return names


FINDING = re.compile(r"^\s*\d+[.)]\s*(?P<text>.+?)\s*$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,60}$")


def parse_findings(text: str, files: set[str], walked: set[str] | None = None) -> list[dict]:
    """Numbered lines 'text | file: x | test: y' -> [{text, file, test}]. A file not in the input or an odd test name becomes None."""
    out = []
    for line in text.splitlines():
        m = FINDING.match(line)
        if not m:
            continue
        parts = [p.strip() for p in m.group("text").split("|")]
        if re.match(r"(?i)^(no|nothing|none)\b", parts[0]):      # "No functional change": a statement, not a finding
            continue
        f = {"text": parts[0][:300], "file": None, "test": None, "via": None}
        for p in parts[1:]:
            k, _, v = p.partition(":")
            v = v.strip().strip("`")
            if v.lower() in ("none", "n/a", "na", "-", ""):       # the model's way of saying "nothing here"
                continue
            if k.strip().lower() == "file" and v in files:
                f["file"] = v
            elif k.strip().lower() == "test" and IDENT.match(v):
                f["test"] = v
            elif k.strip().lower() == "via" and v in (walked or set()):     # a finding may only cite a graph walk that actually happened
                f["via"] = v
        out.append(f)
    return out[:4]


def render(findings: list[dict]) -> str:
    L = []
    for i, f in enumerate(findings, 1):
        L.append(f"{i}. {f['text']}" + (f" (`{f['file']}`)" if f["file"] else "") + (f" [graph walk: {f['via']}]" if f.get("via") else ""))
    tests = [(i, f["test"]) for i, f in enumerate(findings, 1) if f["test"]]
    if tests:
        L += ["", "Suggested tests (not written; a person or the agent can add them):"] + [f"- `{t}`: would catch concern {i}" for i, t in tests]
    return "\n".join(L)


WALK_SYSTEM = """You are the Skeptic. Your only job is to find ways a code change could break production. You cannot approve, merge or edit anything.
You get the diff and the names of the edited symbols. You do NOT get the blast radius, the tests or the history: walk the code graph for them
with the tools (blast_radius, impacted_tests, path_between, recall, read_symbol, list_symbols). Make at most 4 tool calls, then answer.
Write at most 4 findings, one per line, in exactly this form:
1. <one sentence naming the function, test or caller and why it could break> | file: <path from the input, or none> | test: <a snake_case test name that would catch it> | via: <the tool whose result supports it, or none>
Only claim what the diff or a tool result shows. If you found no real risk, write one line starting with "No" instead of inventing findings.
Finish with exactly one last line: RISK: low, RISK: medium or RISK: high.
high = the change likely breaks behaviour or tests fail. medium = real unknowns: untested code, a central function, many callers or importers, or an earlier failed attempt. low = small and covered by tests that pass."""

WALK_TOOLS = ("blast_radius", "impacted_tests", "path_between", "recall", "read_symbol", "list_symbols")
MAX_WALKS = 4


def walk_review(ctx: dict, tb, provider=None, client=None) -> tuple[str, str, list[dict], list[str]]:
    """The Skeptic with read-only graph tools. Returns (body, risk, findings, walks); walks lists the calls it actually made."""
    from .llm_agent import _compact, _dispatch, make_provider
    from .specs import ACTION_TOOLS, GRAPH_TOOLS
    tools = [t for t in GRAPH_TOOLS + ACTION_TOOLS if t["name"] in WALK_TOOLS]
    p = make_provider(provider, None, client, tools=tools, system=WALK_SYSTEM)
    start = (f"PR: {ctx['title']}\nEdited symbols: {', '.join(ctx['edited']) or 'none'}\n"
             f"Files touched (use these exact paths in `file:`): {', '.join(ctx.get('files', [])) or 'none'}\n"
             f"New source files: {', '.join(ctx['new_files']) or 'none'}; tests changed in this PR: {', '.join(ctx['changed_tests']) or 'none'}\n"
             f"Tests that failed because of this PR: {', '.join(ctx['tests']['new_failures']) or 'none'}\n\nDIFF:\n{ctx['diff'][:6000]}")
    p.start(start)
    walks: list[dict] = []
    text = ""
    for _ in range(MAX_WALKS + 2):
        uses = p.step()
        if not uses:
            text = p.last_text
            break
        outs = []
        for u in uses:
            if u["name"] in WALK_TOOLS and len(walks) < MAX_WALKS:
                walks.append({"tool": u["name"], "args": u["input"]})
                outs.append((u["id"], _compact(_dispatch(tb, u["name"], u["input"]))))
            else:        # off the allowlist, or out of walks: say so and make it answer
                outs.append((u["id"], '{"outcome":"error","note":"no more graph walks: answer now in the required format"}'))
        p.results(outs)
    else:
        p.nudge("Answer now in the required format. No more tool calls.")
        p.step()
        text = p.last_text
    called = {w["tool"] for w in walks}
    m = list(RISK_RE.finditer(text))
    if not m and text:
        try:
            again = complete("Reply with exactly one line: RISK: low, RISK: medium or RISK: high.", f"{start[:3000]}\n\nYour review:\n{text}", max_tokens=400)
            w = re.search(r"\b(low|medium|high)\b", again, re.I)
            m = [w] if w else []
        except Exception:
            m = []
    risk = m[-1].group(1).lower() if m else "unknown"
    body = RISK_RE.sub("", text).strip()
    findings = parse_findings(body, set(ctx.get("files", [])) | set(ctx["new_files"]) | set(ctx["changed_tests"]), called)
    shown = render(findings) if findings else re.sub(r"\s*\|\s*file:.*$", "", body, flags=re.M)
    trail = ("\n\n_Graph walks the Skeptic made: " + ", ".join(f"`{w['tool']}`" for w in walks) + "._") if walks else "\n\n_The Skeptic made no graph walks, so its findings rest on the diff alone._"
    risk, raised = floor(risk, ctx)
    note = f"\n\n_Verdict raised to {risk} by the graph: {raised}._" if raised else ""
    return (f"{shown}{trail}{note}\n\n<!-- keel:findings {json.dumps(findings)} -->\nRISK: {risk}", risk, findings, [w["tool"] for w in walks])


ORDER = {"unknown": -1, "low": 0, "medium": 1, "high": 2}


def floor(risk: str, ctx: dict) -> tuple[str, str | None]:
    """The model's verdict can be raised by what the graph and the tests already proved, never lowered. Returns (risk, why-it-was-raised).
    A change that breaks the tests the graph selected is high, whatever the model says; untested new code or a graph lane of
    human_review is at least medium. An 'unknown' verdict (model could not run) is left alone: the gate treats that as a human decision."""
    if risk == "unknown":
        return risk, None
    want, why = risk, None
    if ctx["tests"]["new_failures"]:
        want, why = "high", "tests the graph selected now fail"
    elif ctx["risk"]["lane"] == "human_review" and ORDER[risk] < ORDER["medium"]:
        want, why = "medium", "the graph put this change in the human_review lane"
    elif ctx["new_files"] and not ctx["changed_tests"] and ORDER[risk] < ORDER["medium"]:
        want, why = "medium", "new source files come with no tests"
    return (want, why) if ORDER[want] > ORDER[risk] else (risk, None)


def review(ctx: dict, tb=None) -> tuple[str, str, list[dict]]:
    """With a Toolbox on the base graph the Skeptic walks it; without one it reads the facts prepared in context.json."""
    if tb is not None and have_key():
        try:
            body, risk, findings, _ = walk_review(ctx, tb)
            return body, risk, findings
        except Exception as e:      # fall back to the one-shot review rather than posting nothing
            print(f"graph-walk review failed ({type(e).__name__}); using the one-shot review")
    return review_oneshot(ctx)


def review_oneshot(ctx: dict) -> tuple[str, str, list[dict]]:
    """-> (body, risk, findings) with risk in low|medium|high|unknown."""
    try:
        text = complete(SYSTEM, facts(ctx), max_tokens=1500)    # reasoning models spend part of this before they write the answer
    except LLMUnavailable as e:
        return (f"The Skeptic could not run: {e}. No verdict.\n\nRISK: unknown", "unknown", [])
    except Exception as e:                       # provider error: say so, never fake a verdict
        return (f"The Skeptic could not run ({type(e).__name__}). No verdict.\n\nRISK: unknown", "unknown", [])
    m = list(RISK_RE.finditer(text))
    if not m and text:      # smaller models often skip the last line: ask once for just that line
        try:
            again = complete("Reply with exactly one line: RISK: low, RISK: medium or RISK: high. high = likely breaks behaviour or tests fail; "
                             "medium = real unknowns; low = small and covered by passing tests.", f"{facts(ctx)[:3500]}\n\nYour review:\n{text}", max_tokens=400)
            w = re.search(r"\b(low|medium|high)\b", again, re.I)
            m = [w] if w else []
        except Exception:
            m = []
    risk = m[-1].group(1).lower() if m else "unknown"
    body = RISK_RE.sub("", text).strip()
    findings = parse_findings(body, set(ctx.get("files", [])) | set(ctx["new_files"]) | set(ctx["changed_tests"]))
    # no findings parsed (e.g. "No functional change"): show the model's words without the machine-readable `| file: | test:` tail
    shown = render(findings) if findings else re.sub(r"\s*\|\s*file:.*$", "", body, flags=re.M)
    unknown = sorted({t for t in TICK.findall(body) if t not in known_names(ctx)})
    note = f"\n\n_Names the graph and diff do not contain, treat with care: {', '.join('`'+u+'`' for u in unknown)}._" if unknown else ""
    # the findings travel with the comment (hidden JSON) so the audit ledger can store them as Finding nodes later
    risk, raised = floor(risk, ctx)
    if raised:
        note += f"\n\n_Verdict raised to {risk} by the graph: {raised}._"
    return (f"{shown}{note}\n\n<!-- keel:findings {json.dumps(findings)} -->\nRISK: {risk}", risk, findings)


def open_graph(ctx: dict, repo_dir: str, subdir: str):
    """Build the base commit's graph and return (Toolbox, cleanup). (None, no-op) if FalkorDB is not reachable."""
    import pathlib, shutil, subprocess, tempfile
    try:
        from . import graph as G, pipeline as P
        from .tools import Toolbox
        db = G.connect(); db.list_graphs()
    except Exception as e:
        print(f"no graph to walk ({type(e).__name__}); the Skeptic will read the prepared facts")
        return None, (lambda: None)
    repo = pathlib.Path(repo_dir).resolve()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="keel-skeptic-")).resolve()
    wt = tmp / "base"
    subprocess.run(["git", "worktree", "add", "--detach", "-f", str(wt), ctx["base"]], cwd=repo, check=True, capture_output=True)
    case = f"skeptic{ctx['pr']}"
    P.build_for(db, "demoapp", case, repo, wt, ctx["base"], subdir=subdir)
    tb = Toolbox(db, "demoapp", case, wt / subdir, now=ctx.get("at"))

    def cleanup():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return tb, cleanup


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.skeptic")
    ap.add_argument("--context", default="ci-out/context.json"); ap.add_argument("--repo"); ap.add_argument("--pr")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-dir", help="a git checkout with the base commit: build its graph so the Skeptic can walk it"); ap.add_argument("--subdir", default="demo-app")
    a = ap.parse_args(argv)
    ctx = json.loads(pathlib.Path(a.context).read_text())
    tb, cleanup = None, (lambda: None)
    if a.repo_dir:
        tb, cleanup = open_graph(ctx, a.repo_dir, a.subdir)
    try:
        body, risk, _ = review(ctx, tb)
    finally:
        cleanup()
    pathlib.Path(a.context).with_name("skeptic.txt").write_text(body)
    print(body)
    if not a.dry_run and a.repo and a.pr:
        print("comment", upsert_comment(a.repo, a.pr, "skeptic", "### Skeptic review\n\n" + body))


if __name__ == "__main__":
    main()

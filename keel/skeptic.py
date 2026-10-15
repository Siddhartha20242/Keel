"""The Skeptic: reads the diff plus the graph facts and tries to break the change. It cannot approve, merge or edit; its only
output is one review ending in `RISK: low|medium|high`. If no model is available it says so and gives no verdict."""
from __future__ import annotations
import argparse, json, pathlib, re
from .ghio import upsert_comment
from .llm import LLMUnavailable, complete

SYSTEM = """You are the Skeptic. Your only job is to find ways a code change could break production. You cannot approve, merge or edit anything.
You get the diff and facts from a code graph: blast radius, tests, risk, history, earlier attempts.
Write at most 4 numbered concerns. Each is one sentence naming the specific function, test or caller it concerns and why.
Use only names that appear in the input. If the facts show no real risk, say that in one line instead of inventing concerns.
Finish with exactly one last line: RISK: low, RISK: medium or RISK: high.
high = the change likely breaks behaviour or tests fail. medium = real unknowns: untested code, a central function, many callers,
or an earlier failed attempt. low = small and covered by tests that pass."""

RISK_RE = re.compile(r"^\s*RISK:\s*(low|medium|high)\s*$", re.I | re.M)
TICK = re.compile(r"`([A-Za-z_][\w./]*)`")


def facts(ctx: dict) -> str:
    t = ctx["tests"]
    lines = [
        f"PR: {ctx['title']}",
        f"Edited symbols: {', '.join(ctx['edited']) or 'none'}",
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


def review(ctx: dict) -> tuple[str, str]:
    """-> (body, risk) with risk in low|medium|high|unknown."""
    try:
        text = complete(SYSTEM, facts(ctx), max_tokens=600)
    except LLMUnavailable as e:
        return (f"The Skeptic could not run: {e}. No verdict.\n\nRISK: unknown", "unknown")
    except Exception as e:                       # provider error: say so, never fake a verdict
        return (f"The Skeptic could not run ({type(e).__name__}). No verdict.\n\nRISK: unknown", "unknown")
    m = list(RISK_RE.finditer(text))
    risk = m[-1].group(1).lower() if m else "unknown"
    body = RISK_RE.sub("", text).strip()
    unknown = sorted({t for t in TICK.findall(body) if t not in known_names(ctx)})
    if unknown:
        body += f"\n\n_Names the graph and diff do not contain, treat with care: {', '.join('`'+u+'`' for u in unknown)}._"
    return (f"{body}\n\nRISK: {risk}", risk)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.skeptic")
    ap.add_argument("--context", default="ci-out/context.json"); ap.add_argument("--repo"); ap.add_argument("--pr")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ctx = json.loads(pathlib.Path(a.context).read_text())
    body, risk = review(ctx)
    pathlib.Path(a.context).with_name("skeptic.txt").write_text(body)
    print(body)
    if not a.dry_run and a.repo and a.pr:
        print("comment", upsert_comment(a.repo, a.pr, "skeptic", "### Skeptic review\n\n" + body))


if __name__ == "__main__":
    main()

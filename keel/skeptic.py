"""The Skeptic: reads the diff plus the graph facts and tries to break the change. It cannot approve, merge or edit; its only
output is one review ending in `RISK: low|medium|high`. If no model is available it says so and gives no verdict."""
from __future__ import annotations
import argparse, json, pathlib, re
from .ghio import upsert_comment
from .llm import LLMUnavailable, complete

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


def parse_findings(text: str, files: set[str]) -> list[dict]:
    """Numbered lines 'text | file: x | test: y' -> [{text, file, test}]. A file not in the input or an odd test name becomes None."""
    out = []
    for line in text.splitlines():
        m = FINDING.match(line)
        if not m:
            continue
        parts = [p.strip() for p in m.group("text").split("|")]
        if re.match(r"(?i)^(no|nothing|none)\b", parts[0]):      # "No functional change": a statement, not a finding
            continue
        f = {"text": parts[0][:300], "file": None, "test": None}
        for p in parts[1:]:
            k, _, v = p.partition(":")
            v = v.strip().strip("`")
            if k.strip().lower() == "file" and v in files:
                f["file"] = v
            elif k.strip().lower() == "test" and IDENT.match(v):
                f["test"] = v
        out.append(f)
    return out[:4]


def render(findings: list[dict]) -> str:
    L = []
    for i, f in enumerate(findings, 1):
        L.append(f"{i}. {f['text']}" + (f" (`{f['file']}`)" if f["file"] else ""))
    tests = [(i, f["test"]) for i, f in enumerate(findings, 1) if f["test"]]
    if tests:
        L += ["", "Suggested tests (not written; a person or the agent can add them):"] + [f"- `{t}`: would catch concern {i}" for i, t in tests]
    return "\n".join(L)


def review(ctx: dict) -> tuple[str, str, list[dict]]:
    """-> (body, risk, findings) with risk in low|medium|high|unknown."""
    try:
        text = complete(SYSTEM, facts(ctx), max_tokens=600)
    except LLMUnavailable as e:
        return (f"The Skeptic could not run: {e}. No verdict.\n\nRISK: unknown", "unknown", [])
    except Exception as e:                       # provider error: say so, never fake a verdict
        return (f"The Skeptic could not run ({type(e).__name__}). No verdict.\n\nRISK: unknown", "unknown", [])
    m = list(RISK_RE.finditer(text))
    risk = m[-1].group(1).lower() if m else "unknown"
    body = RISK_RE.sub("", text).strip()
    findings = parse_findings(body, set(ctx.get("files", [])) | set(ctx["new_files"]) | set(ctx["changed_tests"]))
    # no findings parsed (e.g. "No functional change"): show the model's words without the machine-readable `| file: | test:` tail
    shown = render(findings) if findings else re.sub(r"\s*\|\s*file:.*$", "", body, flags=re.M)
    unknown = sorted({t for t in TICK.findall(body) if t not in known_names(ctx)})
    note = f"\n\n_Names the graph and diff do not contain, treat with care: {', '.join('`'+u+'`' for u in unknown)}._" if unknown else ""
    # the findings travel with the comment (hidden JSON) so the audit ledger can store them as Finding nodes later
    return (f"{shown}{note}\n\n<!-- keel:findings {json.dumps(findings)} -->\nRISK: {risk}", risk, findings)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.skeptic")
    ap.add_argument("--context", default="ci-out/context.json"); ap.add_argument("--repo"); ap.add_argument("--pr")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ctx = json.loads(pathlib.Path(a.context).read_text())
    body, risk, _ = review(ctx)
    pathlib.Path(a.context).with_name("skeptic.txt").write_text(body)
    print(body)
    if not a.dry_run and a.repo and a.pr:
        print("comment", upsert_comment(a.repo, a.pr, "skeptic", "### Skeptic review\n\n" + body))


if __name__ == "__main__":
    main()

"""risk-gate: reads the Skeptic's verdict (only from the pipeline's own bot) and the graph lane, and decides how much human
approval the change needs. Fails only when the change is blocked: RISK: high, or graph-selected tests the PR broke.
A human always has the final say; this never approves anything."""
from __future__ import annotations
import argparse, json, pathlib, sys
from .ghio import MARK, latest_trusted, list_comments
from .skeptic import RISK_RE


def parse_risk(body: str) -> str:
    m = list(RISK_RE.finditer(body or ""))
    return m[-1].group(1).lower() if m else "unknown"


def decide(have_review: bool, risk: str, lane: str, new_failures: list[str]) -> tuple[str, str, int]:
    """-> (decision line, level, exit code)"""
    if new_failures:
        return ("DECISION: blocked (tests the graph selected now fail)", "blocked", 1)
    if not have_review:
        return ("DECISION: no Skeptic review yet - human approval required", "human", 0)
    if risk == "high":
        return ("DECISION: blocked (RISK: high)", "blocked", 1)
    if risk == "unknown":
        return ("DECISION: Skeptic gave no verdict - human approval required", "human", 0)
    if risk == "medium" or lane == "human_review":
        why = "RISK: medium" if risk == "medium" else f"graph lane: {lane}"
        return (f"DECISION: human approval required ({why})", "human", 0)
    return (f"DECISION: one human approval (RISK: {risk}, graph lane: {lane})", "standard", 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.gate")
    ap.add_argument("--context", default="ci-out/context.json"); ap.add_argument("--repo"); ap.add_argument("--pr")
    ap.add_argument("--comments-file", help="read comments from a JSON file instead of the API (tests, local runs)")
    a = ap.parse_args(argv)
    ctx = json.loads(pathlib.Path(a.context).read_text())
    comments = json.loads(pathlib.Path(a.comments_file).read_text()) if a.comments_file else list_comments(a.repo, a.pr)
    c = latest_trusted(comments, MARK["skeptic"])
    risk = parse_risk(c["body"]) if c else "unknown"
    line, level, code = decide(bool(c), risk, ctx["risk"]["lane"], ctx["tests"]["new_failures"])
    out = {"decision": line, "level": level, "skeptic_risk": risk, "graph_lane": ctx["risk"]["lane"], "have_review": bool(c)}
    pathlib.Path(a.context).with_name("decision.json").write_text(json.dumps(out))
    print(line)
    print(f"  skeptic: {risk if c else 'no review'} | graph lane: {ctx['risk']['lane']} | ignored comments from other authors: "
          f"{sum(1 for x in comments if MARK['skeptic'] in (x.get('body') or '')) - (1 if c else 0)}")
    sys.exit(code)


if __name__ == "__main__":
    main()

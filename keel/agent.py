"""Decide-and-act pipeline.

Two entry points share one Toolbox:
  * replay(..., mode="oracle"): applies the real fix from git history, then runs every graph-driven decision.
    Deterministic, no API key. This is what the evaluation uses.
  * replay(..., mode="llm"): Claude localizes, edits and runs tests through the same tools; the gate is identical.
"""
from __future__ import annotations
import json, os, pathlib, subprocess, time
from . import graph as G, pipeline as P, risk
from .evaluate import changed_symbols
from .extract import extract_code
from .pr import accepts, build_body
from .tools import Toolbox

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_BUDGET = 40


def load_case(case_id: str) -> dict:
    return json.loads((ROOT / "data" / "cases" / f"{case_id}.json").read_text())


def _git(wd, *a, check=True):
    return subprocess.run(["git", *a], cwd=wd, capture_output=True, text=True, check=check)


reset_worktree = P.reset_worktree


def apply_oracle(case, wd) -> None:
    diff = _git(P.REPO_DIR, "diff", case["parent"], case["fix_commit"], "--", "src", "tests").stdout
    p = subprocess.run(["git", "apply", "-"], cwd=wd, input=diff, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr)


MUTATIONS = [(" is None", " is not None"), (" == ", " != "), (" is not None", " is None"), (" and ", " or "), ("True", "False"), (" in ", " not in ")]


def apply_mutation(code, wd, qname) -> str:
    """Break one line inside the symbol (flip a comparison). Used to show the failure path: graph-selected tests catch it."""
    s = next(x for x in code["symbols"] if x["qname"] == qname)
    path = wd / s["path"]
    lines = path.read_text().splitlines(keepends=True)
    # the patched file has moved lines; locate the symbol again by name
    start = next((i for i, l in enumerate(lines) if f"def {s['name']}(" in l), None)
    if start is None:
        raise RuntimeError("symbol not found after patch")
    for i in range(start + 1, min(start + 80, len(lines))):
        for old, new in MUTATIONS:
            if old in lines[i] and not lines[i].lstrip().startswith(("#", '"')):
                lines[i] = lines[i].replace(old, new, 1)
                path.write_text("".join(lines))
                return f"{s['path']}:{i + 1}: `{old.strip()}` -> `{new.strip()}`"
    raise RuntimeError("no mutable line found")


def decide_and_act(tb: Toolbox, case: dict, edited_syms: list[str], summary: str, extra_test_files=(), run=True, baseline_failures: set | None = None) -> dict:
    """Everything after the patch exists: blast radius, test selection, run, risk, lane, reviewers, memory, PR body."""
    qn, _ = tb.resolve(edited_syms)
    recall = tb.recall(qn) if qn else {"outcome": "not_in_graph"}
    for s in qn[:2]:
        tb.blast_radius(s, depth=3)
    tests = tb.impacted_tests(qn, depth=2, limit=TEST_BUDGET) if qn else {"tests": []}
    result = {"outcome": "no_symbols"}
    if run:
        ids = [t["test"] for t in tests.get("tests", [])]
        result = tb.run_tests(ids, extra_files=list(extra_test_files))
        if "failed" not in result:      # nothing runnable was selected (e.g. no test executes the edit): record that, do not crash
            result = {"ran": 0, "passed": True, "failed": [], "returncode": 0, "missing": [], "tail": result.get("note", "")}
        # only failures the patch introduced gate the PR: tests already failing before the patch are environment noise
        new = sorted(set(result["failed"]) - (baseline_failures or set()))
        result["new_failures"] = new
        result["passed"] = not new and (result["returncode"] in (0, 1))
        tb.evidence["run"] = result
    rk = tb.risk_score(qn) if qn else None
    prior_bad = any(a["outcome"] in ("failed_tests", "reverted", "rejected") for a in recall.get("attempts", []))
    failed = bool(run and not tb.evidence.get("run", {}).get("passed", True))
    lane, why = risk.lane(rk["score"] if rk else 0.0, tests_failed=failed, prior_reverted=prior_bad, untested=not tests.get("selected"))
    tb.reviewers(qn) if qn else None
    issue = {"number": case["issue_number"], "title": case["title"]}
    body = build_body(issue, tb.evidence, lane, why, tb.edited, summary)
    outcome = "failed_tests" if failed else "tests_passed"
    tb.remember(case["title"], summary, outcome, lane, qn)
    return {"lane": lane, "reasons": why, "risk": rk["score"] if rk else None, "components": rk["components"] if rk else None,
            "tests_selected": tests.get("selected"), "suite": tests.get("suite"), "tests_run": tb.evidence.get("run", {}).get("ran"),
            "tests_passed": tb.evidence.get("run", {}).get("passed"), "pr_body": body, "edited": qn, "recalled": len(recall.get("attempts", []))}


def replay(db, case_id: str, mode: str = "oracle", rebuild=False, model=None, out_dir: pathlib.Path | None = None, provider=None) -> dict:
    case = load_case(case_id)
    name = G.code_graph_name("flask", case_id)
    if rebuild or name not in db.list_graphs():
        P.build(db, case_id, case["parent"])
    wd = P.checkout(case_id, case["parent"])
    reset_worktree(wd)
    code = extract_code(wd)
    tb = Toolbox(db, "flask", case_id, wd, now=_git(P.REPO_DIR, "log", "-1", "--format=%ad", "--date=short", case["fix_commit"]).stdout.strip())
    text = case["title"] + "\n" + case["body"][:2500]
    tb.localize(text)
    if mode in ("oracle", "mutant"):
        edited = changed_symbols(case, code)
        pre = tb.impacted_tests(tb.resolve(edited)[0], depth=2, limit=TEST_BUDGET)       # selection is made BEFORE the patch exists
        baseline = set(tb.run_tests([t["test"] for t in pre["tests"]], extra_files=list(case["truth_tests"]))["failed"])   # same test set as the post-patch run
        tb.evidence.pop("run", None)
        apply_oracle(case, wd)
        for f in case["truth_src"]:
            tb.edited[f] = [s for s in edited if s.startswith("flask.") and _path_of(code, s) == f]
        summary = f"Replays the upstream fix for this issue (commit {case['fix_commit'][:8]}) so the decision pipeline can be evaluated."
        if mode == "mutant":
            how = apply_mutation(code, wd, edited[0])
            summary = f"DELIBERATELY BROKEN replay ({how}) to exercise the failure path."
        res = decide_and_act(tb, case, edited, summary, extra_test_files=case["truth_tests"], baseline_failures=baseline)
    else:
        from .llm_agent import run_agent
        res = run_agent(tb, case, text, model=model, provider=provider)
    res["case"] = case_id
    res["trace"] = tb.trace
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{case_id}.md").write_text(res["pr_body"])
    return res


def _path_of(code, q):
    return next((s["path"] for s in code["symbols"] if s["qname"] == q), None)

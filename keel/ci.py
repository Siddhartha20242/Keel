"""Analyse one pull request against the code graph of its base commit.

    python -m keel.ci analyze --repo-dir . --subdir demo-app --base <sha> --head <sha> --pr 7 --title "..." --out ci-out

Builds the graph from the BASE commit (code, per-test coverage, git history), maps the diff onto it, runs the graph-selected
tests on base and on head (only failures the PR introduces count), then scores risk, picks a lane, suggests reviewers and
recalls earlier attempts. Writes ci-out/context.json for the Skeptic, the gate and the Reporter.
"""
from __future__ import annotations
import argparse, json, pathlib, shutil, subprocess, sys, tempfile
from . import graph as G, pipeline as P, risk
from .diffmap import changed_paths, parse_hunks, symbols_from_hunks
from .extract import extract_code
from . import ledger as L
from .memory_io import load_into_graph, read_records
from .tools import Toolbox

DIFF_CHARS = 9000


def _git(repo_dir, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=check).stdout


def analyze(repo_dir, subdir, base, head, pr, title, out_dir, memory_file=None, repo_name="demoapp", db=None) -> dict:
    repo_dir, out_dir = pathlib.Path(repo_dir).resolve(), pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{subdir.rstrip('/')}/" if subdir else ""
    db = db or G.connect()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="keel-ci-")).resolve()
    wts = {}
    try:
        for name, sha in (("base", base), ("head", head)):
            wts[name] = tmp / name
            subprocess.run(["git", "worktree", "add", "--detach", "-f", str(wts[name]), sha], cwd=repo_dir, check=True, capture_output=True)
        base_root, head_root = wts["base"] / subdir, wts["head"] / subdir
        case_id = f"pr{pr}"

        _, stats = P.build_for(db, repo_name, case_id, repo_dir, wts["base"], base, subdir=subdir)
        mem_n = load_into_graph(db, repo_name, read_records(memory_file))
        day = _git(repo_dir, "log", "-1", "--format=%ad", "--date=short", head).strip()
        tb = Toolbox(db, repo_name, case_id, base_root, now=day)
        tb_head = Toolbox(db, repo_name, case_id, head_root, now=day)

        scope = ["--", f"{prefix}src", f"{prefix}tests"] if subdir else ["--", "src", "tests"]
        diff0 = _git(repo_dir, "diff", "-U0", "--no-color", base, head, *scope)
        paths = changed_paths(_git(repo_dir, "diff", "--name-status", base, head, *scope), prefix)
        code = extract_code(base_root)
        edited_all = symbols_from_hunks(parse_hunks(diff0, prefix), code["symbols"])
        edited = [q for q in edited_all if tb.resolve([q])[0]]
        test_files = [p for p in paths["added"] + paths["modified"] if p.startswith("tests/")]
        new_src = [p for p in paths["added"] if p.startswith("src/")]
        full_diff = _git(repo_dir, "diff", "--no-color", base, head, "--", prefix.rstrip("/") or ".")

        recall = tb.recall(edited) if edited else {"outcome": "not_in_graph"}
        blast = {s: tb.blast_radius(s, depth=3) for s in edited[:3]}
        tests = tb.impacted_tests(edited, depth=2, limit=40) if edited else {"selected": 0, "suite": stats["tests"], "tests": []}
        ids = [t["test"] for t in tests.get("tests", [])]
        run = {"ran": 0, "passed": True, "failed": [], "new_failures": [], "missing": []}
        if ids or test_files:
            before = tb.run_tests(ids, extra_files=test_files) if (ids or test_files) else None
            after = tb_head.run_tests(ids, extra_files=test_files)
            base_failed = set(before["failed"]) if before else set()
            new = sorted(set(after["failed"]) - base_failed)
            run = {"ran": after["ran"], "passed": not new and after["returncode"] in (0, 1), "failed": after["failed"],
                   "new_failures": new, "missing": after.get("missing", []), "preexisting_failures": sorted(base_failed)}
        rk = tb.risk_score(edited) if edited else None
        prior_bad = any(a["outcome"] in ("failed_tests", "reverted", "rejected") for a in recall.get("attempts", []))
        # new code the base graph cannot know about counts as untested unless the PR brings tests
        untested = (not tests.get("selected")) and not test_files
        lane, why = risk.lane(rk["score"] if rk else 0.0, tests_failed=bool(run["new_failures"]), prior_reverted=prior_bad, untested=untested)
        if new_src and not test_files:
            lane = "human_review" if lane == "human_review" else "canary"
            why.append(f"new source file(s) {', '.join(new_src)} have no tests in this PR")
        sym_path = {s["qname"]: s["path"] for s in code["symbols"]}
        files = sorted({sym_path[q] for q in edited if q in sym_path} | set(new_src))
        rv = tb.reviewers(edited) if edited else {"reviewers": []}
        cw = tb.changes_with([f for f in files if f.startswith("src/")]) if files else {"outcome": "not_in_graph"}

        led_path = pathlib.Path(memory_file).with_name("ledger.jsonl") if memory_file else None
        led_ok, led_msg = L.verify(L.read(led_path)) if led_path else (True, "no ledger yet")
        ctx = {
            "files": files, "ledger": {"ok": led_ok, "msg": led_msg},
            "pr": pr, "title": title, "base": base, "head": head, "at": day,
            "edited": edited, "new_files": new_src, "changed_tests": test_files, "deleted": paths["deleted"],
            "graph": {"stats": stats, "memory_records": mem_n},
            "tests": {"selected": tests.get("selected", 0), "suite": tests.get("suite", 0),
                      "first": [{"test": t["test"], "hops": t["hops"]} for t in tests.get("tests", [])[:8]], **run},
            "risk": {"score": rk["score"] if rk else 0.0, "components": rk["components"] if rk else {}, "lane": lane, "reasons": why},
            "blast": {s: {"total": b.get("total", 0), "routes": [c["via"] for c in b.get("callers", [])[:5]], "outcome": b["outcome"]}
                      for s, b in blast.items()},
            "reviewers": rv.get("reviewers", [])[:3],
            "changes_with": [p for p in cw.get("partners", []) if p not in files and p not in test_files],
            "memory": recall.get("attempts", []),
            "diff": full_diff[:DIFF_CHARS], "diff_truncated": len(full_diff) > DIFF_CHARS,
            "trace": tb.trace[-40:],
        }
        (out_dir / "context.json").write_text(json.dumps(ctx, indent=1, default=str))
        return ctx
    finally:
        for w in wts.values():
            subprocess.run(["git", "worktree", "remove", "--force", str(w)], cwd=repo_dir, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.ci")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--repo-dir", default="."); a.add_argument("--subdir", default="demo-app")
    a.add_argument("--base", required=True); a.add_argument("--head", required=True)
    a.add_argument("--pr", required=True); a.add_argument("--title", default="")
    a.add_argument("--out", default="ci-out"); a.add_argument("--memory", default=None)
    args = ap.parse_args(argv)
    ctx = analyze(args.repo_dir, args.subdir, args.base, args.head, args.pr, args.title, args.out, args.memory)
    t = ctx["tests"]
    print(f"edited: {ctx['edited'] or 'none'} | new files: {ctx['new_files'] or 'none'}")
    print(f"tests: selected {t['selected']}/{t['suite']}, ran {t['ran']}, new failures {t['new_failures'] or 'none'}")
    print(f"risk {ctx['risk']['score']} -> lane {ctx['risk']['lane']} {ctx['risk']['reasons']}")


if __name__ == "__main__":
    main()

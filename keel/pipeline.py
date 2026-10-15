"""Build the code graph for a replay case (or HEAD) end to end."""
from __future__ import annotations
import pathlib, subprocess, json, datetime as dt
from . import graph as G
from .extract import extract_code, extract_history
from .coverage_map import run_coverage, covers_edges

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_DIR = ROOT / "targets" / "flask"
WORK = ROOT / "data" / "work"


def checkout(case_id: str, sha: str) -> pathlib.Path:
    WORK.mkdir(parents=True, exist_ok=True)
    wd = WORK / case_id
    if not wd.exists():
        subprocess.run(["git", "worktree", "add", "-f", str(wd), sha, "-q"], cwd=REPO_DIR, check=True)
    reset_worktree(wd)      # always the clean parent commit: a previous replay may have patched this directory
    return wd


def reset_worktree(wd: pathlib.Path) -> None:
    subprocess.run(["git", "checkout", "-q", "--", "."], cwd=wd, check=True)
    subprocess.run(["git", "clean", "-qfd", "--", "src", "tests"], cwd=wd, check=True)


def build(db, case_id: str, sha: str, repo="flask", with_coverage=True):
    wd = checkout(case_id, sha)
    code = extract_code(wd)
    hist = extract_history(wd, sha)
    covers = []
    if with_coverage:
        cov = WORK / f"{case_id}.cov"
        if cov.exists():
            cov.unlink()
        if run_coverage(wd, cov):
            covers = covers_edges(wd, cov, code["symbols"])
    day = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", sha], cwd=REPO_DIR, capture_output=True, text=True).stdout.strip()
    meta = {"repo": repo, "ref": sha, "ref_ts": G._ts(day)}
    g = G.load_code_graph(db, G.code_graph_name(repo, case_id), code, hist, covers, meta)
    return g, G.stats(g)

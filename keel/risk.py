"""Transparent risk gate. A weighted sum of five numbers that each come from a graph query. Hand-set weights, shown in every PR."""
from __future__ import annotations
import math

WEIGHTS = {"centrality": 0.30, "reach": 0.25, "coverage_gap": 0.20, "churn": 0.15, "reverts": 0.10}
LANES = [(0.30, "auto_merge"), (0.55, "canary"), (1.01, "human_review")]


def components(sym_rows: list[dict], file_rows: list[dict], total_code: int) -> dict:
    if not sym_rows:
        return {k: 0.0 for k in WEIGHTS}
    centrality = max(r["symbols_below"] / max(total_code, 1) for r in sym_rows)       # PageRank percentile
    reach = min(1.0, math.log1p(max(r["callers"] for r in sym_rows)) / math.log1p(40))  # callers within 3 hops
    uncovered = sum(1 for r in sym_rows if r["tests"] == 0) / len(sym_rows)           # no test executes it
    churn = min(1.0, sum(r["commits_last_year"] for r in file_rows) / 30) if file_rows else 0.0
    reverts = min(1.0, sum(r["reverted"] for r in file_rows) / 2) if file_rows else 0.0
    return {"centrality": centrality, "reach": reach, "coverage_gap": uncovered, "churn": churn, "reverts": reverts}


def score(comp: dict) -> float:
    return sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)


def lane(s: float, tests_failed: bool = False, prior_reverted: bool = False, untested: bool = False) -> tuple[str, list[str]]:
    why = []
    name = next(n for lim, n in LANES if s < lim)
    if untested and name == "auto_merge":
        name = "canary"; why.append("no recorded test executes the edited code, so never auto-merge")
    if tests_failed:
        name = "human_review"; why = ["selected tests failed"]
    if prior_reverted:
        name = "human_review"; why.append("memory: an earlier attempt on these symbols failed its tests or was rejected")
    return name, why

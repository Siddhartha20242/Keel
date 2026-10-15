"""Retrieval-only baseline: the same lexical signal, but no graph. This is the ablation ("take FalkorDB out")."""
from __future__ import annotations
import math, pathlib
from collections import defaultdict
from .extract import extract_code
from .tools import extract_terms


def localize_no_graph(workdir: pathlib.Path, issue_text: str, k: int = 5) -> list[str]:
    syms = [s for s in extract_code(workdir)["symbols"] if s["kind"] in ("code", "class")]
    df = defaultdict(int)
    for s in syms:
        for w in set(s["words"]):
            df[w] += 1
    terms = extract_terms(issue_text)
    scored = defaultdict(list)
    for s in syms:
        w = set(s["words"])
        n = len(syms)
        sc = sum(math.log(n / df[t]) ** 2 for t in terms if t in w and 0 < df[t] <= max(8, n // 12))   # identical to seed_symbols.cypher
        if sc and s["path"].startswith("src/"):
            scored[s["path"]].append(sc)
    fs = {f: sum(sorted(v, reverse=True)[:3]) for f, v in scored.items()}
    return sorted(fs, key=fs.get, reverse=True)[:k]


def tests_same_name(edited_files: list[str], all_test_files: list[str]) -> list[str]:
    """Common heuristic: src/flask/foo.py -> tests/test_foo.py."""
    out = []
    for f in edited_files:
        stem = pathlib.Path(f).stem
        out += [t for t in all_test_files if pathlib.Path(t).stem == f"test_{stem}"]
    return sorted(set(out))

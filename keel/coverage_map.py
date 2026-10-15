"""Per-test dynamic coverage -> (test, symbol) COVERS edges.

Runs the target's own test-suite under coverage.py with dynamic_context=test_function,
then maps each covered line to the smallest enclosing symbol. No LLM, no guessing.
"""
from __future__ import annotations
import os, pathlib, subprocess, sys, tempfile, json
import coverage


def run_coverage(workdir: pathlib.Path, datafile: pathlib.Path, timeout=900) -> bool:
    rc = datafile.with_suffix(".rc")
    rc.write_text(f"[run]\ndynamic_context = test_function\nsource = {workdir/'src'}\ndata_file = {datafile}\nbranch = False\n")
    env = dict(os.environ, PYTHONPATH=str(workdir / "src"))
    p = subprocess.run([sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "--continue-on-collection-errors", "tests", "-o", "addopts="],
                       cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout)
    return datafile.exists()


def covers_edges(workdir: pathlib.Path, datafile: pathlib.Path, symbols: list[dict]) -> list[dict]:
    data = coverage.CoverageData(basename=str(datafile)); data.read()
    by_file: dict[str, list[dict]] = {}
    for s in symbols:
        if s["kind"] in ("code",):
            by_file.setdefault(s["path"], []).append(s)
    test_qnames = {s["qname"] for s in symbols if s["kind"] == "test"}
    edges: dict[tuple[str, str], int] = {}
    for fpath in data.measured_files():
        rel = os.path.relpath(fpath, workdir)
        syms = by_file.get(rel)
        if not syms:
            continue
        syms = sorted(syms, key=lambda s: s["end"] - s["start"])
        for line, ctxs in data.contexts_by_lineno(fpath).items():
            owner = next((s for s in syms if s["start"] <= line <= s["end"]), None)
            if not owner:
                continue
            for c in ctxs:
                if not c:
                    continue
                tid = c.split("|")[0]        # 'test_basic.test_x' or 'test_basic.Class.test_x'
                if not tid.startswith("tests."):
                    tid = "tests." + tid
                if tid not in test_qnames:       # e.g. flask.app.Flask.test_client matches the test_* name filter
                    continue
                edges[(tid, owner["qname"])] = edges.get((tid, owner["qname"]), 0) + 1
    return [{"test": t, "sym": s, "lines": n} for (t, s), n in edges.items()]

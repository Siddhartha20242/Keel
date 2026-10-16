"""How often does the Fixer make the failing tests pass? Runs it N times on the broken demo branch (real model calls, real tokens).
    python scripts/fixer_bench.py 3"""
import pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
from keel import fix as F

ROOT = pathlib.Path(__file__).resolve().parents[1]
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
modes = ["fixer"]
git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout
sha = git("rev-parse", "origin/demo/break").strip()
log = (ROOT / "data" / "bench_failure_log.txt").read_text()
for mode in modes:
    ok, t0 = [], time.time()
    for i in range(n):
        try:
            res = F.fix(ROOT, "demo-app", sha, 100 + i, "tests failing", log, kind="ci")
            ok.append(bool(res.get("fix_verified")))
            subprocess.run(["git", "worktree", "remove", "--force", res["worktree"]], cwd=ROOT, capture_output=True)
        except Exception as e:
            ok.append(False); print(f"  run {i}: {type(e).__name__}: {str(e)[:100]}")
        print(f"  {mode} run {i}: {'verified' if ok[-1] else 'not verified'}", flush=True)
    print(f"{mode}: {sum(ok)}/{n} verified, {time.time() - t0:.0f}s")

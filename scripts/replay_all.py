"""Replay every case in date order against a fresh memory graph and print the lane table."""
import json, glob, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from keel import graph as G, agent as A

db = G.connect()
try:
    db.select_graph(G.memory_graph_name("flask")).delete()
except Exception:
    pass
cases = [json.load(open(f)) for f in glob.glob(str(A.ROOT / "data/cases/*.json"))]
day = lambda c: subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", c["fix_commit"]], cwd=A.P.REPO_DIR, capture_output=True, text=True).stdout.strip()
cases.sort(key=day)
print(f"{'case':9}{'date':11}{'lane':14}{'risk':6}{'sel/suite':>10}{'ran':>5}  tests    recalled  title")
for c in cases:
    r = A.replay(db, c["id"], "oracle", out_dir=A.ROOT / "out")
    print(f"{c['id']:9}{day(c):11}{r['lane']:14}{r['risk']:<6.2f}{str(r['tests_selected'])+'/'+str(r['suite']):>10}{r['tests_run']:>5}  {'pass' if r['tests_passed'] else 'FAIL':8}{r['recalled']:>5}     {c['title'][:48]}")

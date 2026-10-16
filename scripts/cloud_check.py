"""Check a remote FalkorDB (for example FalkorDB Cloud) end to end against Keel's persistent store.

Put the connection in .env (never in chat or in git):
    KEEL_STORE_HOST=...   KEEL_STORE_PORT=...   KEEL_STORE_USERNAME=...   KEEL_STORE_PASSWORD=...   KEEL_STORE_SSL=1
then:  python scripts/cloud_check.py

It writes to throwaway graphs named keel_audit_cloudcheck* and deletes them at the end. It does not touch other graphs.
"""
import json, os, pathlib, statistics, sys, tempfile, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ["KEEL_STORE_REPO"] = f"cloudcheck{int(time.time()) % 100000}"

from keel import ledger, memory_io, persist

BLOB = {"id": "pr1", "pr": 1, "title": "cloud check", "at": "2026-10-16", "symbols": ["shortener.store.Store.shorten"], "files": ["src/shortener/store.py"],
        "tests_selected": 5, "new_failures": [], "risk": 0.2, "lane": "canary", "skeptic_risk": "medium", "decision": "d", "level": "human",
        "graph_lane": "canary", "run": "1", "findings": [{"text": "x", "file": "src/shortener/store.py", "test": "test_x", "via": "blast_radius"}]}
results = []


def check(name, fn):
    t = time.perf_counter()
    try:
        out = fn()
        results.append((name, True, f"{(time.perf_counter() - t) * 1000:.0f} ms", out if isinstance(out, str) else ""))
    except Exception as e:
        results.append((name, False, f"{(time.perf_counter() - t) * 1000:.0f} ms", f"{type(e).__name__}: {str(e)[:140]}"))


try:
    db = persist.persistent_db()
except Exception as e:
    print(f"could not connect: {type(e).__name__}: {str(e)[:200]}")
    raise SystemExit(1)
if db is None:
    print("No store configured. Set KEEL_STORE_HOST (and the port, username, password, SSL) in .env, then run this again.")
    raise SystemExit(2)
repo = persist.repo_name()
d = pathlib.Path(tempfile.mkdtemp())

def roundtrip():
    g = db.select_graph(f"keel_audit_{repo}")
    g.query("RETURN 1")
    ts = []
    for _ in range(20):
        t = time.perf_counter(); g.ro_query("RETURN 1"); ts.append((time.perf_counter() - t) * 1000)
    return f"p50 {statistics.median(ts):.0f} ms, max {max(ts):.0f} ms over 20 queries"

check("connect and query round trip", roundtrip)
check("append a PR's 4 records and verify the chain from the graph", lambda: (ledger.append_graph(db, repo, ledger.stage_entries(BLOB, "merged")), ledger.verify_graph(db, repo)[1])[1])
check("a second PR extends the chain", lambda: (ledger.append_graph(db, repo, ledger.stage_entries({**BLOB, "pr": 2}, "rejected")), ledger.verify_graph(db, repo)[1])[1])

def tamper():
    g = db.select_graph(f"keel_audit_{repo}")
    rec = [r for r in ledger.read_graph(db, repo) if r["kind"] == "skeptic"][0]
    g.query("MATCH (a:AgentRun {seq: $s}) SET a.rec = $r", {"s": rec["seq"], "r": json.dumps({**rec, "summary": "forged"}, sort_keys=True)})
    ok, msg = ledger.verify_graph(db, repo)
    g.query("MATCH (a:AgentRun {seq: $s}) SET a.rec = $r", {"s": rec["seq"], "r": json.dumps(rec, sort_keys=True)})      # restore
    assert not ok, "tampering was NOT detected"
    return "detected: " + msg[:70]

check("editing a stored record is detected", tamper)
check("verification is clean again after restoring it", lambda: ledger.verify_graph(db, repo)[1])
check("explain walks HAS_RUN and PRODUCED", lambda: f"{len(ledger.explain(db, repo, 2))} actions for PR 2")

def findings():
    from keel import graph as G
    rows = db.select_graph(f"keel_audit_{repo}").ro_query(G.read_query("findings_for_pr"), {"pr": 1}).result_set
    assert rows and rows[0][3] == "test_x", rows
    return f"Finding -> File, Finding -> Test edges present: {rows[0]}"

check("findings link to files and suggested tests", findings)
check("memory attempt written and read back", lambda: (memory_io.record_attempt(db, repo, {"id": "pr1", "symbols": ["a.b"], "outcome": "merged", "at": "2026-10-16", "title": "t", "lane": "canary"}), f"{len(memory_io.read_attempts_graph(db, repo))} attempt(s)")[1])

def backup():
    f = d / "ledger.jsonl"
    ledger.write_all(f, ledger.read_graph(db, repo))
    assert ledger.read(f) == ledger.read_graph(db, repo) and ledger.verify(ledger.read(f))[0]
    return "JSONL backup equals the graph and verifies on its own"

check("the JSONL backup is rebuilt from the graph", backup)
check("clean up the throwaway graphs", lambda: [db.select_graph(n).delete() for n in (f"keel_audit_{repo}", f"keel_mem_{repo}")] and "deleted")

w = max(len(r[0]) for r in results)
for name, ok, ms, note in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<{w}}  {ms:>7}  {note}")
print(f"\n{sum(r[1] for r in results)}/{len(results)} checks passed against {os.environ['KEEL_STORE_HOST']}")
raise SystemExit(0 if all(r[1] for r in results) else 1)

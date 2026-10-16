"""The persistent FalkorDB store: the graph is the source of truth for the audit ledger and memory, JSONL is the backup."""
import json
import pytest

from keel import ledger, memory_cli, memory_io, persist
from keel.ghio import MARK

REPO = "pytest_store"
BLOB = {"id": "pr7", "pr": 7, "title": "t", "at": "2026-10-16", "symbols": ["shortener.store.Store.shorten"], "files": ["src/shortener/store.py"],
        "tests_selected": 5, "new_failures": [], "risk": 0.2, "lane": "canary", "skeptic_risk": "medium",
        "decision": "DECISION: human approval required (RISK: medium)", "level": "human", "graph_lane": "canary", "run": "42",
        "findings": [{"text": "x", "file": "src/shortener/store.py", "test": "test_x"}]}


@pytest.fixture()
def db(monkeypatch):
    try:
        from keel import graph as G
        d = G.connect(); d.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    monkeypatch.setenv("KEEL_STORE_HOST", "localhost")
    monkeypatch.setenv("KEEL_STORE_REPO", REPO)
    for name in (f"keel_audit_{REPO}", f"keel_mem_{REPO}"):
        try:
            d.select_graph(name).delete()
        except Exception:
            pass
    yield d
    for name in (f"keel_audit_{REPO}", f"keel_mem_{REPO}"):
        try:
            d.select_graph(name).delete()
        except Exception:
            pass


def comments(pr, outcome_blob):
    return [{"id": 1, "user": {"login": "github-actions[bot]"}, "created_at": "2026-10-16T00:00:00Z",
             "body": f"{MARK['report']}\nx\n<!-- keel:data {json.dumps(outcome_blob)} -->"}]


def test_appended_records_chain_and_verify_from_the_graph(db):
    a = ledger.append_graph(db, REPO, ledger.stage_entries(BLOB, "merged"))
    b = ledger.append_graph(db, REPO, ledger.stage_entries({**BLOB, "pr": 8}, "rejected"))
    assert b[0]["prev"] == a[-1]["hash"] and b[0]["seq"] == 4
    ok, msg = ledger.verify_graph(db, REPO)
    assert ok and "8 records" in msg and "from the graph" in msg
    assert [r["seq"] for r in ledger.read_graph(db, REPO)] == list(range(8))


def test_tampering_with_the_graph_is_detected(db):
    ledger.append_graph(db, REPO, ledger.stage_entries(BLOB, "merged"))
    g = db.select_graph(f"keel_audit_{REPO}")
    rec = [r for r in ledger.read_graph(db, REPO) if r["kind"] == "skeptic"][0]
    forged = {**rec, "data": {**rec["data"], "risk": "low"}}
    g.query("MATCH (a:AgentRun {seq: $s}) SET a.rec = $r", {"s": rec["seq"], "r": json.dumps(forged, sort_keys=True)})
    ok, msg = ledger.verify_graph(db, REPO)
    assert not ok and f"record {rec['seq']}" in msg
    with pytest.raises(RuntimeError, match="refusing to extend a broken ledger"):
        ledger.append_graph(db, REPO, ledger.stage_entries({**BLOB, "pr": 9}, "merged"))


def test_deleting_a_chain_edge_in_the_graph_is_detected(db):
    ledger.append_graph(db, REPO, ledger.stage_entries(BLOB, "merged"))
    db.select_graph(f"keel_audit_{REPO}").query("MATCH (:AgentRun {seq: 1})-[n:NEXT]->(:AgentRun {seq: 2}) DELETE n")
    ok, msg = ledger.verify_graph(db, REPO)
    assert not ok and "NEXT edges" in msg


def test_memory_cli_writes_the_store_first_and_rebuilds_the_backup(db, tmp_path):
    cf, mem, led = tmp_path / "c.json", tmp_path / "m.jsonl", tmp_path / "l.jsonl"
    cf.write_text(json.dumps(comments(7, BLOB)))
    memory_cli.main(["record", "--repo", "o/r", "--pr", "7", "--outcome", "merged", "--file", str(mem), "--ledger", str(led), "--comments-file", str(cf)])
    graph_recs = ledger.read_graph(db, REPO)
    assert len(graph_recs) == 4 and ledger.read(led) == graph_recs               # the backup is exactly the graph's content
    assert [r["id"] for r in memory_io.read_attempts_graph(db, REPO)] == ["pr7"]
    assert [r["id"] for r in memory_io.read_records(mem)] == ["pr7"]
    cf.write_text(json.dumps(comments(8, {**BLOB, "id": "pr8", "pr": 8, "at": "2026-10-17"})))
    memory_cli.main(["record", "--repo", "o/r", "--pr", "8", "--outcome", "rejected", "--file", str(mem), "--ledger", str(led), "--comments-file", str(cf)])
    assert ledger.verify(ledger.read(led))[0] and len(ledger.read(led)) == 8
    assert [r["id"] for r in memory_io.read_records(mem)] == ["pr7", "pr8"]


def test_an_empty_store_is_bootstrapped_from_an_existing_backup(db, tmp_path):
    led, mem = tmp_path / "l.jsonl", tmp_path / "m.jsonl"
    ledger.append(led, ledger.stage_entries({**BLOB, "pr": 3}, "merged"))
    memory_io.append_record(mem, {"id": "pr3", "symbols": ["a.b"], "outcome": "merged", "at": "2026-10-10", "title": "t", "lane": "canary"})
    cf = tmp_path / "c.json"
    cf.write_text(json.dumps(comments(7, BLOB)))
    memory_cli.main(["record", "--repo", "o/r", "--pr", "7", "--outcome", "merged", "--file", str(mem), "--ledger", str(led), "--comments-file", str(cf)])
    recs = ledger.read_graph(db, REPO)
    assert len(recs) == 8 and ledger.verify_graph(db, REPO)[0]                   # old chain kept, new one continues it
    assert [r["id"] for r in memory_io.read_attempts_graph(db, REPO)] == ["pr3", "pr7"]


def test_without_a_store_it_falls_back_to_the_jsonl_files(monkeypatch, tmp_path):
    monkeypatch.delenv("KEEL_STORE_HOST", raising=False)
    assert persist.persistent_db() is None
    cf, mem, led = tmp_path / "c.json", tmp_path / "m.jsonl", tmp_path / "l.jsonl"
    cf.write_text(json.dumps(comments(7, BLOB)))
    memory_cli.main(["record", "--repo", "o/r", "--pr", "7", "--outcome", "merged", "--file", str(mem), "--ledger", str(led), "--comments-file", str(cf)])
    assert len(ledger.read(led)) == 4 and ledger.verify(ledger.read(led))[0]

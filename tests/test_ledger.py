"""Audit ledger: hash chain, tamper detection, and the graph walk that explains a PR."""
import json
import pytest

from keel import ledger, memory_cli
from keel.ghio import MARK

BLOB = {"id": "pr7", "pr": 7, "title": "t", "at": "2026-10-16", "symbols": ["shortener.store.Store.shorten"], "files": ["src/shortener/store.py"],
        "tests_selected": 5, "new_failures": [], "risk": 0.2, "lane": "canary", "skeptic_risk": "medium",
        "decision": "DECISION: human approval required (RISK: medium)", "level": "human", "graph_lane": "canary", "run": "42"}


def build(tmp_path, prs=(7, 8)):
    f = tmp_path / "ledger.jsonl"
    for pr in prs:
        ledger.append(f, ledger.stage_entries({**BLOB, "pr": pr}, "merged"))
    return f


def test_chain_verifies_and_links_records(tmp_path):
    f = build(tmp_path)
    recs = ledger.read(f)
    assert ledger.verify(recs)[0] and len(recs) == 8
    assert recs[0]["prev"] == ledger.GENESIS and all(recs[i]["prev"] == recs[i - 1]["hash"] for i in range(1, 8))
    assert [r["kind"] for r in recs[:4]] == ["analyze", "skeptic", "gate", "outcome"]


def test_editing_a_verdict_is_detected(tmp_path):
    f = build(tmp_path)
    lines = f.read_text().splitlines()
    r = json.loads(lines[5]); r["data"]["risk"] = "low"; lines[5] = json.dumps(r, sort_keys=True)
    f.write_text("\n".join(lines) + "\n")
    ok, msg = ledger.verify(ledger.read(f))
    assert not ok and "record 5" in msg and "edited" in msg


def test_removing_or_reordering_a_record_is_detected(tmp_path):
    f = build(tmp_path)
    lines = f.read_text().splitlines()
    f.write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    assert not ledger.verify(ledger.read(f))[0]
    f.write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")
    assert not ledger.verify(ledger.read(f))[0]


def test_empty_ledger_is_fine(tmp_path):
    assert ledger.verify(ledger.read(tmp_path / "none.jsonl")) == (True, "0 records, chain intact")


def test_memory_cli_extends_the_ledger_and_refuses_a_broken_one(tmp_path):
    comment = {"id": 1, "user": {"login": "github-actions[bot]"}, "created_at": "2026-10-16T00:00:00Z",
               "body": f"{MARK['report']}\nx\n<!-- keel:data {json.dumps(BLOB)} -->"}
    cf, mem, led = tmp_path / "c.json", tmp_path / "m.jsonl", tmp_path / "l.jsonl"
    cf.write_text(json.dumps([comment]))
    args = ["record", "--repo", "o/r", "--pr", "7", "--outcome", "merged", "--file", str(mem), "--ledger", str(led), "--comments-file", str(cf)]
    memory_cli.main(args)
    assert len(ledger.read(led)) == 4 and ledger.verify(ledger.read(led))[0]
    lines = led.read_text().splitlines()
    r = json.loads(lines[1]); r["summary"] = "forged"; lines[1] = json.dumps(r, sort_keys=True)
    led.write_text("\n".join(lines) + "\n")
    with pytest.raises(SystemExit) as e:
        memory_cli.main(args)
    assert "broken ledger" in str(e.value)


def test_explain_walks_the_graph(tmp_path):
    try:
        from keel import graph as G
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    f = build(tmp_path)
    ledger.load_into_graph(db, "pytest", ledger.read(f))
    rows = ledger.explain(db, "pytest", 8)
    assert [r["kind"] for r in rows] == ["analyze", "skeptic", "gate", "outcome"]
    assert {r["kind"]: r["verdict"] for r in rows}["skeptic"] == "medium"
    g = db.select_graph("keel_audit_pytest")
    assert g.ro_query("MATCH (:AgentRun)-[n:NEXT]->(:AgentRun) RETURN count(n)").result_set[0][0] == 7
    assert g.ro_query("MATCH (:PullRequest)-[:TOUCHES]->(f:File) RETURN count(DISTINCT f)").result_set[0][0] == 1
    db.select_graph("keel_audit_pytest").delete()


def test_findings_become_graph_nodes_about_files_and_suggesting_tests(tmp_path):
    try:
        from keel import graph as G
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    f = tmp_path / "l.jsonl"
    blob = {**BLOB, "findings": [{"text": "unchecked body", "file": "src/shortener/server.py", "test": "test_big_body"},
                                 {"text": "dedupe", "file": None, "test": None}]}
    ledger.append(f, ledger.stage_entries(blob, "merged"))
    ledger.load_into_graph(db, "pytest2", ledger.read(f))
    g = db.select_graph("keel_audit_pytest2")
    from keel import graph as G2
    rows = g.ro_query(G2.read_query("findings_for_pr"), {"pr": 7}).result_set
    assert rows == [[0, "unchecked body", "src/shortener/server.py", "test_big_body"], [1, "dedupe", None, None]]
    g.delete()

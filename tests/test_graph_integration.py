"""Integration tests against a live FalkorDB (skipped if none). Uses the replay graph for case 53b8f082; build it with
`python -m keel.evaluate --rebuild` first."""
import json, pytest
from keel import graph as G, agent as A, pipeline as P
from keel.tools import Toolbox

CASE = "53b8f082"


@pytest.fixture(scope="module")
def tb():
    try:
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    if G.code_graph_name("flask", CASE) not in db.list_graphs():
        pytest.skip("graph not built")
    c = A.load_case(CASE)
    A.reset_worktree(P.checkout(CASE, c["parent"]))
    return Toolbox(db, "flask", CASE, P.checkout(CASE, c["parent"]))


def test_unknown_symbol_runs_no_query(tb):
    n = len(tb.trace)
    out = tb.blast_radius("flask.payment_service.charge")
    assert out["outcome"] == "unknown_symbol" and len(tb.trace) == n + 1   # only the resolve query ran


def test_injection_string_is_just_an_unknown_name(tb):
    out = tb.blast_radius("x'}) MATCH (n) DETACH DELETE n //")
    assert out["outcome"] == "unknown_symbol"
    assert tb.g.ro_query("MATCH (n:Symbol) RETURN count(n)").result_set[0][0] > 0


def test_blast_radius_returns_paths_and_depth_is_capped(tb):
    out = tb.blast_radius("flask.testing.FlaskClient.open", depth=99)
    assert out["depth"] == 4
    if out["outcome"] == "answered_with_path":
        assert all(r["via"][0] == r["symbol"] and r["via"][-1] == out["symbol"] for r in out["callers"])


def test_impacted_tests_select_focused_tests_first(tb):
    out = tb.impacted_tests(["flask.testing.FlaskClient.open"])
    assert out["selected"] > 0 and out["selected"] < out["suite"]
    assert out["tests"][0]["hops"] == 0


def test_empty_traversal_is_not_reported_as_safe(tb):
    out = tb.blast_radius("flask.cli.main")       # nothing calls the CLI entry point
    assert out["outcome"] in ("not_in_graph", "answered_with_path")
    if out["outcome"] == "not_in_graph":
        assert "not that nothing depends" in out["note"]


def test_edit_limited_to_files_in_graph(tb):
    assert tb.edit_file("setup.py", "a", "b")["outcome"] == "unknown_symbol"


def test_pagerank_tool(tb):
    out = tb.load_bearing(5)
    scores = [s["score"] for s in out["symbols"]]
    assert len(scores) == 5 and scores == sorted(scores, reverse=True)

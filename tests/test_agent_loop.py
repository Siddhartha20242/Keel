"""The Claude loop, driven by a scripted fake client (no API key needed): tool dispatch, finish, and the gate afterwards."""
import types, pytest
from keel import graph as G, agent as A, pipeline as P
from keel.llm_agent import run_agent
from keel.tools import Toolbox

CASE = "53b8f082"


def block(type, **kw):
    return types.SimpleNamespace(type=type, **kw)


class FakeClient:
    def __init__(self, script):
        self.script, self.i = script, 0
        self.messages = self

    def create(self, **kw):
        step = self.script[self.i]; self.i += 1
        return types.SimpleNamespace(content=[block("tool_use", id=f"t{self.i}", name=n, input=a) for n, a in step])


def test_loop_edits_runs_tests_then_gate_decides():
    try:
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    if G.code_graph_name("flask", CASE) not in db.list_graphs():
        pytest.skip("graph not built")
    case = A.load_case(CASE); wd = P.checkout(CASE, case["parent"]); A.reset_worktree(wd)
    tb = Toolbox(db, "flask", CASE, wd, now="2025-08-18")
    script = [
        [("localize", {"issue_text": case["title"]})],
        [("edit_file", {"path": "src/flask/testing.py", "old": "    def open(", "new": "    # keel fake-agent edit\n    def open("})],
        [("impacted_tests", {"symbols": ["flask.testing.FlaskClient.open"]})],
        [("blast_radius", {"symbol": "flask.nope.Missing"})],
        [("finish", {"summary": "Touched `flask.testing.FlaskClient.open` and also `Flask.auth_gateway`."})],
    ]
    try:
        res = run_agent(tb, case, case["title"], client=FakeClient(script))
    finally:
        A.reset_worktree(wd)
    assert res["edited"] == ["flask.testing.FlaskClient.open"]
    assert res["lane"] in ("auto_merge", "canary", "human_review")
    assert "auth_gateway" in res["pr_body"] and "rejected" in res["pr_body"]      # invented name caught, flat summary used
    assert any(t["tool_query"] == "resolve_symbols" for t in tb.trace)

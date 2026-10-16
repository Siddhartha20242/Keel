"""The Skeptic walks the code graph with read-only tools, and a finding may only cite a walk it actually made."""
import json, pathlib, subprocess, types
import pytest

from keel import graph as G, pipeline as P, skeptic
from keel.tools import Toolbox

ROOT = pathlib.Path(__file__).resolve().parents[1]


def block(**kw):
    return types.SimpleNamespace(**kw)


class FakeOpenAI:
    """A scripted OpenAI-style client: each entry is ('tool', name, args) or ('text', body)."""
    def __init__(self, script):
        self.script, self.i, self.seen_tools = script, 0, []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.seen_tools = [t["function"]["name"] for t in kw["tools"]]
        kind, *rest = self.script[self.i]; self.i += 1
        if kind == "tool":
            call = block(id=f"c{self.i}", function=block(name=rest[0], arguments=json.dumps(rest[1])))
            msg = block(content="", tool_calls=[call])
        else:
            msg = block(content=rest[0], tool_calls=None)
        return types.SimpleNamespace(choices=[block(message=msg)])


@pytest.fixture(scope="module")
def tb():
    try:
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    wt = ROOT / "data" / "work" / "skeptic-walk-test"
    if not wt.exists():
        subprocess.run(["git", "worktree", "add", "--detach", "-f", str(wt), sha], cwd=ROOT, check=True, capture_output=True)
    P.build_for(db, "demoapp", "skwalk", ROOT, wt, sha, subdir="demo-app")
    return Toolbox(db, "demoapp", "skwalk", wt / "demo-app", now="2026-10-16")


CTX = {"pr": 9, "title": "t", "edited": ["shortener.store.Store.shorten"], "files": ["src/shortener/store.py"], "new_files": [], "changed_tests": [],
       "tests": {"new_failures": []}, "risk": {"lane": "canary"}, "diff": "-            if url in self._by_url:\n+            if url not in self._by_url:"}


def test_the_skeptic_walks_then_cites_only_walks_it_made(tb, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    answer = ("1. Store.shorten now returns a stored code for new URLs, which breaks dedupe | file: src/shortener/store.py | test: test_same_url_same_code | via: blast_radius\n"
              "2. Callers may rely on this | file: none | test: test_x_y | via: path_between\n"          # never called: must be dropped
              "RISK: high")
    client = FakeOpenAI([("tool", "blast_radius", {"symbol": "shortener.store.Store.shorten"}), ("text", answer)])
    body, risk, findings, walks = skeptic.walk_review(CTX, tb, provider="openai", client=client)
    assert walks == ["blast_radius"] and risk == "high"
    assert findings[0]["via"] == "blast_radius" and findings[1]["via"] is None
    assert "[graph walk: blast_radius]" in body and "Graph walks the Skeptic made: `blast_radius`" in body
    assert set(client.seen_tools) == set(skeptic.WALK_TOOLS)          # read-only: no edit_file, no run_tests, no finish


def test_a_skeptic_that_makes_no_walks_says_so(tb, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    client = FakeOpenAI([("text", "1. Looks inverted | file: src/shortener/store.py | test: test_same_url_same_code | via: blast_radius\nRISK: medium")])
    body, risk, findings, walks = skeptic.walk_review(CTX, tb, provider="openai", client=client)
    assert walks == [] and findings[0]["via"] is None                  # it cited a walk it never made
    assert "made no graph walks" in body


def test_walks_are_capped_and_off_list_tools_are_refused(tb, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    script = [("tool", "edit_file", {"path": "x", "old": "a", "new": "b"})] + [("tool", "blast_radius", {"symbol": "shortener.store.Store.shorten"})] * 6 + [("text", "No risk found.\nRISK: low")]
    client = FakeOpenAI(script)
    body, risk, findings, walks = skeptic.walk_review(CTX, tb, provider="openai", client=client)
    assert "edit_file" not in walks and len(walks) <= skeptic.MAX_WALKS

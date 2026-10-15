"""The Fixer must prove a fix by making the tests that failed in CI pass, not by 'no new failures' on a branch that is already red.
Uses the real graph and test runs on the commit of the demo PR that breaks the dedupe check (skipped if that ref or FalkorDB is missing)."""
import pathlib, subprocess, pytest

from keel import fix as F
from keel import graph as G

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ("FAILED tests/test_server.py::test_shorten_redirect_and_stats - assert 400 == 201\n"
       "FAILED tests/test_store.py::test_same_url_same_code - KeyError: 'x'\n"
       "FAILED tests/test_store.py::test_resolve_counts_hits - KeyError: 'x'\n")


@pytest.fixture(scope="module")
def broken_sha():
    try:
        db = G.connect(); db.list_graphs()
    except Exception:
        pytest.skip("FalkorDB not running")
    p = subprocess.run(["git", "rev-parse", "--verify", "-q", "origin/demo/break"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        pytest.skip("origin/demo/break not fetched")
    return p.stdout.strip()


def run_fix(monkeypatch, sha, edit):
    def fake_agent(tb, case, text, model=None, provider=None):
        edit(pathlib.Path(tb.workdir))
        return {"diff": "x", "pr_body": "body"}
    monkeypatch.setattr(F, "run_agent", fake_agent)
    res = F.fix(ROOT, "demo-app", sha, 5, "tests failing", LOG, kind="ci")
    subprocess.run(["git", "worktree", "remove", "--force", res["worktree"]], cwd=ROOT, capture_output=True)
    return res


def test_wrong_edit_on_a_red_branch_is_not_verified(monkeypatch, broken_sha):
    def wrong(wd):          # what the model really did in PR #5: touch an unrelated line
        p = wd / "src/shortener/server.py"
        p.write_text(p.read_text().replace('self.rfile.read(length) or b"{}"', '(self.rfile.read(length) or b"{}").decode()'))
    res = run_fix(monkeypatch, broken_sha, wrong)
    assert res["fix_verified"] is False and len(res["still_failing"]) == 3
    assert "Verified" not in res["pr_body"]


def test_the_real_fix_is_verified(monkeypatch, broken_sha):
    def right(wd):
        p = wd / "src/shortener/store.py"
        p.write_text(p.read_text().replace("if url not in self._by_url:", "if url in self._by_url:"))
    res = run_fix(monkeypatch, broken_sha, right)
    assert res["fix_verified"] is True and "Verified" in res["pr_body"]

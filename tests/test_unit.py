"""Unit tests that need no database: extraction, risk gate, summary guard, term extraction."""
import pathlib, textwrap
from keel import risk
from keel.extract import extract_code
from keel.pr import accepts
from keel.tools import extract_terms


def _repo(tmp_path, files):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return tmp_path


def test_call_resolution_through_imports_self_and_inheritance(tmp_path):
    root = _repo(tmp_path, {
        "src/pkg/__init__.py": "from .util import helper\n",
        "src/pkg/util.py": "def helper():\n    return 1\n",
        "src/pkg/base.py": "class Base:\n    def hook(self):\n        return 0\n    def run(self):\n        return self.hook()\n",
        "src/pkg/app.py": "from .base import Base\nfrom . import helper\nclass App(Base):\n    def hook(self):\n        return helper()\n",
        "tests/test_app.py": "from pkg.app import App\ndef test_it():\n    App().run()\n",
    })
    code = extract_code(root)
    edges = {(c["src"], c["dst"]) for c in code["calls"]}
    assert ("pkg.app.App.hook", "pkg.util.helper") in edges            # re-export through __init__
    assert ("pkg.base.Base.run", "pkg.base.Base.hook") in edges        # self.method
    assert ("pkg.base.Base.run", "pkg.app.App.hook") in edges          # override in a subclass
    kinds = {s["qname"]: s["kind"] for s in code["symbols"]}
    assert kinds["tests.test_app.test_it"] == "test" and kinds["pkg.util.helper"] == "code"


def test_conftest_fixtures_named_test_are_not_tests(tmp_path):
    root = _repo(tmp_path, {"src/pkg/a.py": "def f():\n    pass\n", "tests/conftest.py": "def test_apps():\n    pass\n"})
    kinds = {s["qname"]: s["kind"] for s in extract_code(root)["symbols"]}
    assert kinds["tests.conftest.test_apps"] == "helper"


def test_ambiguous_attribute_calls_get_no_edge(tmp_path):
    methods = "".join(f"class C{i}:\n    def go(self):\n        pass\n" for i in range(5))
    root = _repo(tmp_path, {"src/pkg/m.py": methods + "def caller(x):\n    x.go()\n"})
    assert not [c for c in extract_code(root)["calls"] if c["src"] == "pkg.m.caller"]


def test_risk_lane_thresholds_and_overrides():
    comp = {k: 0.0 for k in risk.WEIGHTS}
    assert risk.lane(risk.score(comp))[0] == "auto_merge"
    assert risk.lane(0.4)[0] == "canary" and risk.lane(0.9)[0] == "human_review"
    assert risk.lane(0.0, tests_failed=True)[0] == "human_review"
    name, why = risk.lane(0.0, prior_reverted=True)
    assert name == "human_review" and "memory" in why[0]
    name, why = risk.lane(0.0, untested=True)
    assert name == "canary" and "no recorded test" in why[0]
    assert abs(sum(risk.WEIGHTS.values()) - 1.0) < 1e-9


def test_summary_guard_rejects_invented_names():
    known = {"flask.app.Flask.dispatch_request"}
    assert accepts("Changed `dispatch_request` in `src/flask/app.py`.", known, {"src/flask/app.py"})[0]
    ok, why = accepts("Also patched `Flask.auth_gateway`.", known, {"src/flask/app.py"})
    assert not ok and "auth_gateway" in why


def test_terms_drop_urls_and_stopwords():
    t = extract_terms("See https://github.com/pallets/flask/issues/1 the SERVER_NAME handling in create_url_adapter")
    assert "server_name" in t and "create_url_adapter" in t and "github" not in t and "the" not in t

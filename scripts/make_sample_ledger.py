"""Build data/sample_ledger.jsonl from three LOCALLY SIMULATED PRs (a harmless doc change, a breaking change, new untested code).
These are not real GitHub PRs and the outcomes are made up to show the ledger; the Skeptic did not run (no model key), so its
verdict is `unknown`. Usage: python scripts/make_sample_ledger.py <dir with ci-safe/ ci-break/ ci-new/ context.json files>"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from keel import gate, ledger

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(__file__).resolve().parents[1] / "data" / "sample_ledger.jsonl"
out.unlink(missing_ok=True)
for n, (name, outcome) in enumerate([("safe", "merged"), ("break", "rejected"), ("new", "rejected")], start=1):
    ctx = json.loads((src / f"ci-{name}" / "context.json").read_text())
    line, level, _ = gate.decide(False, "unknown", ctx["risk"]["lane"], ctx["tests"]["new_failures"])
    blob = {"pr": n, "title": f"simulated PR: {name}", "at": ctx["at"], "symbols": ctx["edited"],
            "files": sorted({"src/" + q.split(".")[0] + "/" + q.split(".")[1] + ".py" for q in ctx["edited"]} | set(ctx["new_files"])),
            "tests_selected": ctx["tests"]["selected"], "new_failures": ctx["tests"]["new_failures"], "risk": ctx["risk"]["score"],
            "lane": ctx["risk"]["lane"], "skeptic_risk": "unknown", "decision": line, "level": level, "graph_lane": ctx["risk"]["lane"], "run": "local"}
    ledger.append(out, ledger.stage_entries(blob, outcome))
print(ledger.verify(ledger.read(out)))

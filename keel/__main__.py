"""python -m keel replay <case> [--mode oracle|llm] | eval | cases | mcp"""
import argparse, json, pathlib, sys
from . import graph as G, agent as A

def main():
    ap = argparse.ArgumentParser(prog="keel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay"); r.add_argument("case"); r.add_argument("--mode", default="oracle", choices=["oracle", "mutant", "llm"])
    r.add_argument("--provider", choices=["anthropic", "openai"]); r.add_argument("--model"); r.add_argument("--rebuild", action="store_true"); r.add_argument("--out", default="out")
    sub.add_parser("cases"); sub.add_parser("eval"); sub.add_parser("mcp")
    a = ap.parse_args()
    if a.cmd == "cases":
        for f in sorted((A.ROOT / "data" / "cases").glob("*.json")):
            c = json.loads(f.read_text()); print(c["id"], c["split"], f"#{c['issue_number']}", c["title"][:70])
    elif a.cmd == "eval":
        from . import evaluate
        rows = evaluate.run(); print(evaluate.summarize(rows))
    elif a.cmd == "mcp":
        from .mcp_server import main as m; m()
    else:
        res = A.replay(G.connect(), a.case, a.mode, rebuild=a.rebuild, out_dir=pathlib.Path(a.out), provider=a.provider, model=a.model)
        print(res["pr_body"]); print("\n---\nlane:", res["lane"], "| risk:", res["risk"], "| tests", res["tests_run"], "ran,", "passed" if res["tests_passed"] else "FAILED")

main()

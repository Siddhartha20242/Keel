"""Claude drives localize -> edit -> test; the merge gate is NOT in the model's hands (decide_and_act runs after `finish`)."""
from __future__ import annotations
import json
from .specs import GRAPH_TOOLS, ACTION_TOOLS
from .pr import accepts
from .tools import Toolbox

SYSTEM = """You are Keel, an agent that turns a GitHub issue into a minimal, safe code change.
Work from the code graph, not from guesses:
1. localize the issue, then recall earlier attempts on the symbols you plan to touch.
2. read_symbol the best candidate, make the smallest edit with edit_file.
3. impacted_tests for the symbols you edited, run_tests on them; fix what you broke.
4. finish with a short summary. Only name symbols/files that tools returned: any name not in the graph is rejected.
Tools never accept names that are not in the graph. If a tool says not_in_graph, say so instead of inventing an answer."""

MAX_STEPS = 16


def _dispatch(tb: Toolbox, name: str, a: dict) -> dict:
    table = {
        "localize": lambda: tb.localize(a.get("issue_text", ""), k=int(a.get("k", 5))),
        "blast_radius": lambda: tb.blast_radius(a["symbol"], int(a.get("depth", 3))),
        "impacted_tests": lambda: tb.impacted_tests(a["symbols"], int(a.get("depth", 2)), limit=40),
        "path_between": lambda: tb.path_between(a["source"], a["target"]),
        "load_bearing": lambda: tb.load_bearing(int(a.get("limit", 10))),
        "recall": lambda: tb.recall(a["symbols"]),
        "read_symbol": lambda: tb.read_symbol(a["symbol"]),
        "edit_file": lambda: tb.edit_file(a["path"], a["old"], a["new"]),
        "run_tests": lambda: tb.run_tests(a["tests"]),
    }
    if name not in table:
        return {"outcome": "error", "note": f"unknown tool {name}"}
    try:
        return table[name]()
    except KeyError as e:
        return {"outcome": "error", "note": f"missing argument {e}"}


def run_agent(tb: Toolbox, case: dict, issue_text: str, model: str = "claude-sonnet-5-5", client=None) -> dict:
    from .agent import decide_and_act
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    tools = GRAPH_TOOLS + ACTION_TOOLS
    msgs = [{"role": "user", "content": f"Issue #{case['issue_number']}: {issue_text}"}]
    summary = ""
    for _ in range(MAX_STEPS):
        resp = client.messages.create(model=model, max_tokens=4000, system=SYSTEM, tools=tools, messages=msgs)
        msgs.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            break
        results, done = [], False
        for u in uses:
            if u.name == "finish":
                summary, done = u.input.get("summary", ""), True
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": "ok"})
                continue
            out = _dispatch(tb, u.name, u.input)
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, default=str)[:12000]})
        msgs.append({"role": "user", "content": results})
        if done:
            break
    edited = [q for qs in tb.edited.values() for q in qs]
    ok, why = accepts(summary, {q for fl in tb.evidence.get("localize", {}).get("files", []) for s in fl["symbols"] for q in [s["symbol"]]} | set(edited),
                      {f["file"] for f in tb.evidence.get("localize", {}).get("files", [])} | set(tb.edited))
    if not ok or not summary:
        summary = ("Edited " + ", ".join(f"`{q}`" for q in edited) + ".") if edited else "No edit was made."
        summary += f" (model summary rejected: {why})" if not ok and summary else ""
    return decide_and_act(tb, case, edited, summary, extra_test_files=[])

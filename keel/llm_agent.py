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


class _Anthropic:
    """Normalises Anthropic's tool-use messages to (tool_calls, append_results)."""
    def __init__(self, model, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.c, self.model = client, model
        self.tools = [{**t} for t in GRAPH_TOOLS + ACTION_TOOLS]

    def start(self, user):
        self.msgs = [{"role": "user", "content": user}]

    def step(self):
        r = self.c.messages.create(model=self.model, max_tokens=4000, system=SYSTEM, tools=self.tools, messages=self.msgs)
        self.msgs.append({"role": "assistant", "content": r.content})
        return [{"id": b.id, "name": b.name, "input": b.input} for b in r.content if b.type == "tool_use"]

    def results(self, outs):
        self.msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": c} for i, c in outs]})


class _OpenAICompatible:
    """Any OpenAI-compatible chat-completions endpoint (OpenAI, Gemini, Groq, OpenRouter, Together, DeepSeek, Ollama...).
    Configure with OPENAI_API_KEY, OPENAI_BASE_URL and KEEL_MODEL."""
    def __init__(self, model, client=None):
        if client is None:
            import openai
            client = openai.OpenAI()
        self.c, self.model = client, model
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                      for t in GRAPH_TOOLS + ACTION_TOOLS]

    def start(self, user):
        self.msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    def step(self):
        r = self.c.chat.completions.create(model=self.model, tools=self.tools, messages=self.msgs)
        m = r.choices[0].message
        calls = m.tool_calls or []
        self.msgs.append({"role": "assistant", "content": m.content or "",
                          **({"tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]} if calls else {})})
        out = []
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out.append({"id": c.id, "name": c.function.name, "input": args})
        return out

    def results(self, outs):
        for i, c in outs:
            self.msgs.append({"role": "tool", "tool_call_id": i, "content": c})


def make_provider(provider: str | None, model: str | None, client=None):
    import os
    provider = provider or os.environ.get("KEEL_PROVIDER") or ("openai" if os.environ.get("OPENAI_API_KEY") else "anthropic")
    if provider == "anthropic":
        return _Anthropic(model or os.environ.get("KEEL_MODEL", "claude-sonnet-5-5"), client)
    return _OpenAICompatible(model or os.environ.get("KEEL_MODEL", "gpt-4o"), client)


def run_agent(tb: Toolbox, case: dict, issue_text: str, model: str | None = None, client=None, provider: str | None = None) -> dict:
    from .agent import decide_and_act
    p = make_provider(provider, model, client)
    p.start(f"Issue #{case['issue_number']}: {issue_text}")
    summary = ""
    for _ in range(MAX_STEPS):
        uses = p.step()
        if not uses:
            break
        outs, done = [], False
        for u in uses:
            if u["name"] == "finish":
                summary, done = u["input"].get("summary", ""), True
                outs.append((u["id"], "ok"))
                continue
            outs.append((u["id"], json.dumps(_dispatch(tb, u["name"], u["input"]), default=str)[:12000]))
        p.results(outs)
        if done:
            break
    edited = [q for qs in tb.edited.values() for q in qs]
    ok, why = accepts(summary, {s["symbol"] for fl in tb.evidence.get("localize", {}).get("files", []) for s in fl["symbols"]} | set(edited),
                      {f["file"] for f in tb.evidence.get("localize", {}).get("files", [])} | set(tb.edited))
    if not ok or not summary:
        summary = ("Edited " + ", ".join(f"`{q}`" for q in edited) + ".") if edited else "No edit was made."
        summary += f" (model summary rejected: {why})" if not ok and summary else ""
    return decide_and_act(tb, case, edited, summary, extra_test_files=[])

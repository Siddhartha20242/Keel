"""Claude drives localize -> edit -> test; the merge gate is NOT in the model's hands (decide_and_act runs after `finish`)."""
from __future__ import annotations
import json, os, subprocess, time
from .specs import GRAPH_TOOLS, ACTION_TOOLS, VALID
from .pr import accepts
from .tools import Toolbox

SYSTEM = """You are Keel: turn a GitHub issue into a minimal safe code change using only the provided tools (exact names, no others).
Order: localize (or list_symbols if it finds nothing) -> recall -> read_symbol -> edit_file -> impacted_tests -> run_tests -> finish.
Never stop without calling finish. If no change is warranted, call finish and say why.
Only name symbols/files that tools returned. If a tool says not_in_graph or unknown_symbol, say so; do not invent."""

MAX_STEPS = 16
RESULT_CHARS = 3000      # tool results are resent every turn; keep them small (small tiers cap tokens/minute)
ISSUE_CHARS = 1200


def _brief_localize(r: dict) -> dict:
    if r.get("outcome") != "answered_with_path":
        return r
    return {"outcome": r["outcome"], "files": [{"file": f["file"], "score": f["score"],
            "symbols": [{"symbol": s["symbol"], "via_tests": s.get("via_tests", [])[:1]} for s in f["symbols"][:3]]} for f in r["files"]]}


def _compact(out: dict) -> str:
    text = json.dumps(out, default=str, separators=(",", ":"))
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + '..."truncated"'


def _dispatch(tb: Toolbox, name: str, a: dict) -> dict:
    table = {
        "localize": lambda: _brief_localize(tb.localize(a.get("issue_text", ""), k=4)),
        "blast_radius": lambda: tb.blast_radius(a["symbol"], int(a.get("depth", 3))),
        "impacted_tests": lambda: tb.impacted_tests(a["symbols"], int(a.get("depth", 2)), limit=40),
        "path_between": lambda: tb.path_between(a["source"], a["target"]),
        "load_bearing": lambda: tb.load_bearing(int(a.get("limit", 10))),
        "recall": lambda: tb.recall(a["symbols"]),
        "list_symbols": lambda: tb.list_symbols(a.get("file", ""), int(a.get("limit", 40))),
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

    def nudge(self, text):
        self.msgs.append({"role": "user", "content": text})

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
        extra = {"reasoning_effort": "low"} if "gpt-oss" in self.model else {}     # keep reasoning out of the reply
        r = self.c.chat.completions.create(model=self.model, tools=self.tools, messages=self.msgs, temperature=0, **extra)
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

    def nudge(self, text):
        self.msgs.append({"role": "user", "content": text})

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
    p.start(f"Issue #{case['issue_number']}: {issue_text[:ISSUE_CHARS]}")
    summary, nudges, waits, finish_nudged = "", 0, 0, False
    for _ in range(MAX_STEPS + 6):
        try:
            uses = p.step()
        except Exception as e:                      # provider errors: recover where it makes sense, otherwise say why
            msg = str(e)
            code = getattr(e, "status_code", None)
            if code == 400 and any(k in msg.lower() for k in ("tool", "parse", "failed_generation")) and nudges < 6:
                nudges += 1                         # bad tool name, or prose where a tool call belonged: say what is valid and retry
                p.nudge(f"Your last reply was not a valid tool call. Reply with exactly one tool call and no prose. Valid tools: {', '.join(VALID)}.")
                continue
            if code in (429, 503) and waits < 3:
                waits += 1; time.sleep(8 * waits); continue
            if code == 413:
                raise RuntimeError("Request too large for this model/tier (tokens per minute). Use a model or plan with a larger limit.") from e
            raise
        if not uses:
            if not summary and not finish_nudged:      # it forgot to call finish: ask once
                finish_nudged = True
                p.nudge("Call the finish tool now with a one-paragraph summary of what you changed and why (or why you changed nothing).")
                continue
            if os.environ.get("KEEL_DEBUG"):
                print("[step] model replied without a tool call; stopping")
            break
        outs, done = [], False
        for u in uses:
            if u["name"] == "finish":
                summary, done = u["input"].get("summary", ""), True
                outs.append((u["id"], "ok"))
                continue
            res_text = _compact(_dispatch(tb, u["name"], u["input"]))
            if os.environ.get("KEEL_DEBUG"):
                print(f"[step] {u['name']}({json.dumps(u['input'])[:160]}) -> {res_text[:200]}")
            outs.append((u["id"], res_text))
        p.results(outs)
        if done:
            break
    edited = [q for qs in tb.edited.values() for q in qs]
    ok, why = accepts(summary, {s["symbol"] for fl in tb.evidence.get("localize", {}).get("files", []) for s in fl["symbols"]} | set(edited),
                      {f["file"] for f in tb.evidence.get("localize", {}).get("files", [])} | set(tb.edited))
    if not ok or not summary:
        summary = ("Edited " + ", ".join(f"`{q}`" for q in edited) + ".") if edited else "No edit was made."
        summary += f" (model summary rejected: {why})" if not ok and summary else ""
    # Baseline: run the same selected tests on the pristine parent so pre-existing failures do not count against the patch.
    from .agent import reset_worktree, _git
    baseline: set = set()
    diff = _git(tb.workdir, "diff", "--", "src", "tests").stdout
    if edited and diff:
        qn, _ = tb.resolve(edited)
        sel = tb.impacted_tests(qn, depth=2, limit=40)
        reset_worktree(tb.workdir)
        baseline = set(tb.run_tests([t["test"] for t in sel["tests"]])["failed"])
        tb.evidence.pop("run", None)
        subprocess.run(["git", "apply", "-"], cwd=tb.workdir, input=diff, text=True, check=True)
    res = decide_and_act(tb, case, edited, summary, baseline_failures=baseline)
    if diff:
        res["diff"] = diff
        section = "\n## Diff written by the model\n```diff\n" + diff[:3500] + "\n```\n"
        for marker in ("\n## Why here (localization)", "\n## Blast radius", "\n## Tests:", "\n## Suggested reviewers", "\n_Opened by Keel"):
            if marker in res["pr_body"]:
                res["pr_body"] = res["pr_body"].replace(marker, section + marker, 1)
                break
        else:
            res["pr_body"] += section
    return res

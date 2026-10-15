"""Tool schemas shared by the Claude agent loop and the MCP server. One catalogue, one set of validated arguments."""

def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}

SYM = {"type": "string", "description": "Fully-qualified symbol name exactly as returned by localize (e.g. flask.app.Flask.dispatch_request). Checked against the graph."}
SYMS = {"type": "array", "items": SYM}

GRAPH_TOOLS = [
    {"name": "localize", "description": "Find where to edit. Seeds from the issue text, spreads over the call graph and git co-change history, and traces through tests that exercise the described behaviour. Returns ranked files with the symbols and why. Call this first.",
     "input_schema": _obj({"issue_text": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 8}}, ["issue_text"])},
    {"name": "blast_radius", "description": "Everything that transitively calls a symbol (up to depth 4), each with the call route. An empty result means 'not modelled', not 'safe'.",
     "input_schema": _obj({"symbol": SYM, "depth": {"type": "integer", "minimum": 1, "maximum": 4}}, ["symbol"])},
    {"name": "impacted_tests", "description": "Tests that executed the given symbols or their callers (dynamic per-test coverage x call graph), focused tests first. Run these, not the whole suite.",
     "input_schema": _obj({"symbols": SYMS, "depth": {"type": "integer", "minimum": 1, "maximum": 3}}, ["symbols"])},
    {"name": "path_between", "description": "Shortest call route between two symbols: why is A connected to B?",
     "input_schema": _obj({"source": SYM, "target": SYM}, ["source", "target"])},
    {"name": "load_bearing", "description": "The most load-bearing code by PageRank over the call graph.",
     "input_schema": _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 20}}, [])},
    {"name": "recall", "description": "Earlier attempts (and their outcomes) that touched these symbols. Check before editing.",
     "input_schema": _obj({"symbols": SYMS}, ["symbols"])},
]

ACTION_TOOLS = [
    {"name": "read_symbol", "description": "Source of one symbol.", "input_schema": _obj({"symbol": SYM}, ["symbol"])},
    {"name": "edit_file", "description": "Replace one exact occurrence of `old` with `new` in a file that exists in the graph.",
     "input_schema": _obj({"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, ["path", "old", "new"])},
    {"name": "run_tests", "description": "Run tests by id (as returned by impacted_tests).",
     "input_schema": _obj({"tests": {"type": "array", "items": {"type": "string"}}}, ["tests"])},
    {"name": "finish", "description": "Done editing. Risk scoring, merge lane, reviewers, memory and the PR body are computed from the graph afterwards; you only supply a one-paragraph summary of what you changed and why.",
     "input_schema": _obj({"summary": {"type": "string"}}, ["summary"])},
]

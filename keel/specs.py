"""Tool schemas shared by the LLM agent loop and the MCP server. Descriptions are deliberately short: every request resends them."""

def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}

SYM = {"type": "string", "description": "qualified symbol name from localize, e.g. flask.app.Flask.dispatch_request"}
SYMS = {"type": "array", "items": SYM}
INT = {"type": "integer"}

GRAPH_TOOLS = [
    {"name": "localize", "description": "Where to edit for an issue: ranked files and symbols. Call first.",
     "input_schema": _obj({"issue_text": {"type": "string"}}, ["issue_text"])},
    {"name": "blast_radius", "description": "Transitive callers of a symbol with call routes. Empty means not modelled, not safe.",
     "input_schema": _obj({"symbol": SYM, "depth": INT}, ["symbol"])},
    {"name": "impacted_tests", "description": "Tests that executed these symbols or their callers, focused first.",
     "input_schema": _obj({"symbols": SYMS}, ["symbols"])},
    {"name": "path_between", "description": "Shortest call route from source to target symbol.",
     "input_schema": _obj({"source": SYM, "target": SYM}, ["source", "target"])},
    {"name": "load_bearing", "description": "Most load-bearing symbols by PageRank.",
     "input_schema": _obj({"limit": INT}, [])},
    {"name": "recall", "description": "Earlier attempts and outcomes on these symbols. Check before editing.",
     "input_schema": _obj({"symbols": SYMS}, ["symbols"])},
]

ACTION_TOOLS = [
    {"name": "read_symbol", "description": "Source code of one symbol.", "input_schema": _obj({"symbol": SYM}, ["symbol"])},
    {"name": "edit_file", "description": "Replace one exact occurrence of old with new in a file.",
     "input_schema": _obj({"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, ["path", "old", "new"])},
    {"name": "run_tests", "description": "Run tests by id from impacted_tests.",
     "input_schema": _obj({"tests": {"type": "array", "items": {"type": "string"}}}, ["tests"])},
    {"name": "finish", "description": "Done. Risk, lane, reviewers and PR body are computed afterwards. Give a short summary.",
     "input_schema": _obj({"summary": {"type": "string"}}, ["summary"])},
]
VALID = [t["name"] for t in GRAPH_TOOLS + ACTION_TOOLS]

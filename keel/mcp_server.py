"""Expose the graph tools over MCP so any MCP client can use them. KEEL_CASE picks the code graph (a replay case id)."""
import json, os
from mcp.server.fastmcp import FastMCP
from . import graph as G, pipeline as P, agent as A
from .tools import Toolbox

def main():
    case_id = os.environ.get("KEEL_CASE", "e82db2ca")
    case = A.load_case(case_id)
    tb = Toolbox(G.connect(), "flask", case_id, P.checkout(case_id, case["parent"]))
    mcp = FastMCP("keel")

    @mcp.tool()
    def localize(issue_text: str, k: int = 5) -> str:
        """Where to edit for this issue: seeds + call graph + co-change + test trace."""
        return json.dumps(tb.localize(issue_text, k=k), default=str)

    @mcp.tool()
    def blast_radius(symbol: str, depth: int = 3) -> str:
        """Transitive callers of a symbol with call routes."""
        return json.dumps(tb.blast_radius(symbol, depth), default=str)

    @mcp.tool()
    def impacted_tests(symbols: list[str], depth: int = 2) -> str:
        """Tests that executed these symbols or their callers."""
        return json.dumps(tb.impacted_tests(symbols, depth, limit=40), default=str)

    @mcp.tool()
    def risk_score(symbols: list[str]) -> str:
        """PageRank/reach/coverage/churn/revert risk for editing these symbols."""
        return json.dumps(tb.risk_score(symbols), default=str)

    @mcp.tool()
    def reviewers(symbols: list[str]) -> str:
        """Who has changed the blast radius of these symbols."""
        return json.dumps(tb.reviewers(symbols), default=str)

    @mcp.tool()
    def path_between(source: str, target: str) -> str:
        """Why is A connected to B? Shortest call route."""
        return json.dumps(tb.path_between(source, target), default=str)

    @mcp.tool()
    def load_bearing(limit: int = 10) -> str:
        """Most load-bearing symbols by PageRank."""
        return json.dumps(tb.load_bearing(limit), default=str)

    @mcp.tool()
    def recall(symbols: list[str]) -> str:
        """Earlier attempts touching these symbols (memory graph)."""
        return json.dumps(tb.recall(symbols), default=str)

    mcp.run()

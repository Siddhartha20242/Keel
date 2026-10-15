// Most load-bearing code by PageRank over CALLS (FalkorDB algo.pageRank), computed live.
CALL algo.pageRank('Symbol', 'CALLS') YIELD node, score
WHERE node.kind = 'code'
RETURN node.qname AS symbol, node.path AS file, score
ORDER BY score DESC
LIMIT $limit

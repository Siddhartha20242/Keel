// Per edited symbol: distinct transitive callers within 3 hops, using FalkorDB's BFS over CALLED_BY (CALLS reversed).
// The earlier `-[:CALLS*1..3]->` pattern enumerated every path: 446 ms per query at 30k nodes against ~3 ms for BFS, same counts.
// Symbols with no callers return no row; the toolbox defaults them to 0.
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})
CALL algo.BFS(s, 3, 'CALLED_BY') YIELD nodes
RETURN s.qname AS symbol, size([n IN nodes WHERE n.kind = 'code']) AS callers

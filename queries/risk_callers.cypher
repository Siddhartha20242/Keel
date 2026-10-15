// Per edited symbol: distinct transitive callers within 3 hops. (Separate query: FalkorDB drops rows when a variable-length
// MATCH follows an aggregating WITH.)
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})
OPTIONAL MATCH (c:Symbol {kind: 'code'})-[:CALLS*1..3]->(s)
RETURN s.qname AS symbol, count(DISTINCT c) AS callers

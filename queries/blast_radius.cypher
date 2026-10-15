// Everything that (transitively) calls the symbol, with the shortest route as evidence.
MATCH p = (c:Symbol {kind: 'code'})-[:CALLS*1..{{DEPTH}}]->(s:Symbol {qname: $qname})
WITH c, p ORDER BY length(p)
WITH c, collect(p)[0] AS best
RETURN c.qname AS symbol, c.path AS file, length(best) AS hops, [n IN nodes(best) | n.qname] AS via
ORDER BY hops, symbol

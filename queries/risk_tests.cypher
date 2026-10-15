// Per edited symbol: how many tests executed it (dynamic coverage).
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})
OPTIONAL MATCH (t:Test)-[:COVERS]->(s)
RETURN s.qname AS symbol, count(DISTINCT t) AS tests

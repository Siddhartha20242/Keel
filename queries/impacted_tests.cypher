// Tests that executed the edited symbols (hops 0) or anything that calls them (hops 1..{{DEPTH}}), from per-test dynamic
// coverage. `breadth` = how many symbols the test touches: a focused test (small breadth) is better evidence than an
// end-to-end test that touches everything. (Two branches because FalkorDB returns nothing for a *0.. pattern after UNWIND.)
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})<-[:COVERS]-(t:Test)
RETURN t.id AS test, t.path AS file, 0 AS hops, s.qname AS edited, s.qname AS through, t.breadth AS breadth
UNION
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})
MATCH p = (c:Symbol)-[:CALLS*1..{{DEPTH}}]->(s)
MATCH (t:Test)-[:COVERS]->(c)
RETURN t.id AS test, t.path AS file, min(length(p)) AS hops, s.qname AS edited, c.qname AS through, t.breadth AS breadth

// Issue -> tests whose text matches (rarity-weighted) -> the code those tests actually executed (dynamic COVERS).
// Code executed by almost every test says nothing, so each symbol is scaled by log(#tests / #tests covering it).
UNWIND $terms AS t
MATCH (x:Test) WHERE t IN x.words
WITH t, collect(x) AS hits
WHERE size(hits) <= $maxdf
UNWIND hits AS x
WITH x, sum(log(toFloat($nt) / size(hits)) ^ 2) AS w
// FalkorDB quirk: a MATCH straight after an aggregating WITH returns nothing; this pass-through WITH fixes it.
WITH x, w ORDER BY w DESC LIMIT 25
MATCH (x)-[:COVERS]->(c:Symbol {kind: 'code'})
WITH c, sum(w) AS raw, collect(DISTINCT x.id)[..2] AS via_tests
RETURN c.qname AS symbol, c.path AS file, raw * log(toFloat($nt) / (1.0 + size((c)<-[:COVERS]-()))) AS score, via_tests
ORDER BY score DESC
LIMIT $limit

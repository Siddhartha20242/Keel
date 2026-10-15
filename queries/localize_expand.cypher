// Spread seed weight over the call graph (both directions, up to {{DEPTH}} hops). A symbol is a good edit site if the
// symbols the issue talks about are its callers or callees, even when its own text never matches.
// Damping: each hop keeps $decay of the weight, and a seed spreads its weight over its neighbours (1/sqrt(degree)),
// so hub symbols (Flask.run, dispatch_request) neither collect everything nor broadcast everything.
UNWIND $seeds AS sd
MATCH (seed:Symbol {qname: sd.qname})
MATCH p = (seed)-[:CALLS*0..{{DEPTH}}]-(c:Symbol)
WHERE c.kind = 'code'
WITH c, seed, sd.w AS w, min(length(p)) AS hops
WITH c, seed, w, hops, size((seed)-[:CALLS]-()) AS deg
RETURN c.qname AS symbol, c.path AS file,
       sum(w * ($decay ^ hops) / sqrt(1.0 + deg * toFloat(hops))) AS score,
       collect(DISTINCT seed.qname)[..3] AS because, min(hops) AS nearest
ORDER BY score DESC
LIMIT $limit

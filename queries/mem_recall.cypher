// Memory graph (one per repo/tenant): earlier attempts that touched these symbols, newest first.
UNWIND $qnames AS q
MATCH (a:Attempt)-[:ABOUT]->(:Sym {qname: q})
WITH DISTINCT a ORDER BY a.at DESC
RETURN a.id AS id, a.issue AS issue, a.summary AS summary, a.outcome AS outcome, a.lane AS lane, a.at AS at
LIMIT $limit

CREATE (a:Attempt {id: $id, issue: $issue, summary: $summary, outcome: $outcome, lane: $lane, at: $at, json: $json})
WITH a
UNWIND $qnames AS q
MERGE (s:Sym {qname: q})
CREATE (a)-[:ABOUT]->(s)

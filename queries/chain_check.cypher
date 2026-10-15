// Is the recorded NEXT chain unbroken? Every run except the first must have exactly one predecessor.
MATCH (a:AgentRun)
OPTIONAL MATCH (p:AgentRun)-[:NEXT]->(a)
WITH a, count(p) AS preds
WHERE preds <> 1 AND a.seq > 0
RETURN a.seq AS broken_at

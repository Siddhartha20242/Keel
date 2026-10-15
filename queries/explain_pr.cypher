// Show every action behind a pull request, in order, with the verdict each one produced. Walks HAS_RUN then PRODUCED.
MATCH (:PullRequest {number: $pr})-[:HAS_RUN]->(a:AgentRun)
OPTIONAL MATCH (a)-[:PRODUCED]->(v:Verdict)
RETURN a.seq AS seq, a.kind AS kind, a.actor AS actor, a.summary AS summary, a.at AS at, a.hash AS hash, v.value AS verdict
ORDER BY a.seq

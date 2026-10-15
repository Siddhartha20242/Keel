// What the Skeptic found for a PR, which file each finding is about and which test it suggests.
MATCH (:PullRequest {number: $pr})-[:HAS_RUN]->(:AgentRun)-[:PRODUCED]->(n:Finding)
OPTIONAL MATCH (n)-[:ABOUT]->(f:File)
OPTIONAL MATCH (n)-[:SUGGESTS]->(t:Test)
RETURN n.idx AS idx, n.text AS finding, f.path AS file, t.name AS suggested_test
ORDER BY n.idx

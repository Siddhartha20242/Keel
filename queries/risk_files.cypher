// Per edited file: recent churn, reverted changes, last touch. $now = the graph's replay point (Meta.ref_ts).
UNWIND $paths AS p
MATCH (f:File {path: p})
OPTIONAL MATCH (c:Commit)-[:TOUCHED]->(f)
RETURN f.path AS file,
       count(c) AS commits,
       count(DISTINCT CASE WHEN c.ts > $now - 31536000 THEN c END) AS commits_last_year,
       sum(c.reverted) AS reverted,
       max(c.ts) AS last_touched

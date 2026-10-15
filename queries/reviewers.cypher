// Who has actually changed the files in the blast radius, weighted toward recent work.
UNWIND $paths AS p
MATCH (f:File {path: p})<-[:TOUCHED]-(c:Commit)<-[:AUTHORED_COMMIT]-(who:Person)
WITH who, count(DISTINCT c) AS commits, collect(DISTINCT f.path)[..3] AS files, max(c.ts) AS last,
     count(DISTINCT CASE WHEN c.ts > $now - 63072000 THEN c END) AS recent
RETURN who.name AS person, commits, recent, files, last
ORDER BY recent DESC, commits DESC
LIMIT $limit

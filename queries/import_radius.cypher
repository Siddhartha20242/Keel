// Module blast radius: how many source files import each edited file, directly or through other files (up to 4 imports deep).
// Separate from the call graph: it catches a changed module that others depend on even when no call edge was resolved.
UNWIND $paths AS p
MATCH (f:File {path: p})
OPTIONAL MATCH (x:File {kind: 'src'})-[:IMPORTS*1..4]->(f)
RETURN f.path AS file, count(DISTINCT x) AS importers

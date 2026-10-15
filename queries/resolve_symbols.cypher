// Resolve names the model (or the issue text) mentioned against the graph. Nothing runs on names that do not resolve.
UNWIND $names AS n
OPTIONAL MATCH (s:Symbol) WHERE s.qname = n OR (s.name = n AND s.kind IN ['code','class'])
RETURN n AS name, collect(DISTINCT s.qname) AS qnames

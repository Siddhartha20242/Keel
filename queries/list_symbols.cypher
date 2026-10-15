// Browse the code graph by name: the most load-bearing code symbols first, optionally for one file. For small repos, or when
// localize finds no lexical match, this lets the agent pick a symbol by reading names instead of guessing.
MATCH (s:Symbol {kind: 'code'})
WHERE $file = '' OR s.path = $file
RETURN s.qname AS symbol, s.path AS file
ORDER BY s.pr DESC
LIMIT $limit

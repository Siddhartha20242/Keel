// Files that historically change together with the candidate files (git history, before the replay point).
UNWIND $paths AS p
MATCH (f:File {path: p})-[c:CO_CHANGED]-(g:File {kind: 'src'})
RETURN p AS file, g.path AS partner, c.n AS times
ORDER BY times DESC

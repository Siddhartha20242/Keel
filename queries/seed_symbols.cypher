// Lexical entry points, weighted by rarity (idf squared): a term shared by many symbols says little, one shared by
// few says a lot; terms in more than $maxdf symbols are ignored. Everything after this query is structure
// (CALLS / CO_CHANGED), not text.
UNWIND $terms AS t
MATCH (s:Symbol) WHERE s.kind IN ['code','class'] AND t IN s.words
WITH t, collect(s) AS hits
WHERE size(hits) <= $maxdf
UNWIND hits AS s
WITH s, sum(log(toFloat($n) / size(hits)) ^ 2) AS w
RETURN s.qname AS symbol, s.path AS file, w AS seed_weight
ORDER BY w DESC
LIMIT $limit

// Per edited symbol: PageRank percentile input (how many code symbols rank below it).
UNWIND $qnames AS q
MATCH (s:Symbol {qname: q})
OPTIONAL MATCH (o:Symbol {kind: 'code'}) WHERE o.pr < s.pr
RETURN s.qname AS symbol, s.pr AS pagerank, count(o) AS symbols_below

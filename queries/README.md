# Queries

One file per step. All are parameterised; the only substitution is a validated integer depth.

| file | used by | what it does |
|---|---|---|
| resolve_symbols | every tool | checks names against the graph first |
| seed_symbols, localize_expand, cochange, test_trace | localize | where to edit: seeds, call-graph spread, co-change, test trace |
| blast_radius | blast_radius | transitive callers with the call route |
| impacted_tests | impacted_tests | tests that ran the symbol or its callers |
| risk_symbols, risk_callers, risk_tests, risk_files | risk_score | PageRank percentile, callers, tests, churn, reverts |
| reviewers | reviewers | who changed the affected files |
| path_between | path_between | shortest call route (`algo.SPpaths`) |
| load_bearing | load_bearing | most load-bearing symbols (`algo.pageRank`) |
| mem_recall, mem_remember | recall, memory write | per-repo memory graph |

FalkorDB quirks worth knowing: a variable-length bound can't be a parameter; `*0..` after `UNWIND` returns nothing; a
`MATCH` right after an aggregating `WITH` silently returns nothing (add a pass-through `WITH x ORDER BY ... LIMIT`);
pattern comprehensions aren't supported.

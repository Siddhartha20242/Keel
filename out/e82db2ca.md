Fixes #5916: `provide_automatic_options` is weird

Replays the upstream fix for this issue (commit e82db2ca) so the decision pipeline can be evaluated.

## Merge lane: **human review**
Risk score **0.536** (weights: centrality 0.25, reach 0.2, import_reach 0.15, coverage_gap 0.2, churn 0.1, reverts 0.1)

| signal | value | what it measures |
|---|---|---|
| centrality | 0.976 | PageRank percentile of the edited symbols over the call graph |
| reach | 0.592 | transitive callers within 3 hops (log scale) |
| import_reach | 0.957 | share of other source files that import the edited files (4 levels) |
| coverage_gap | 0.0 | share of edited symbols that no test executes |
| churn | 0.3 | commits touching these files in the last year |
| reverts | 0.0 | earlier changes to these files that were reverted |


## What changed
- `src/flask/sansio/app.py`: `sansio.app.App.add_url_rule`

## Why here (localization)
- `src/flask/app.py` score 4.758, best symbol `app.Flask.raise_routing_exception` via tests test_provide_automatic_options_attr, test_view_provide_automatic_options_attr
- `src/flask/sansio/app.py` score 3.293, best symbol `sansio.app.App.trap_http_exception` via tests test_provide_automatic_options_attr, test_view_provide_automatic_options_attr
- `src/flask/cli.py` score 3.003, best symbol `cli.routes_command`

## Blast radius of `sansio.app.App.add_url_rule` (8 callers within 3 hops)
- app.Flask.__init__ → sansio.app.App.add_url_rule
- sansio.scaffold.Scaffold.route → sansio.app.App.add_url_rule
- sansio.scaffold.Scaffold._method_route → sansio.scaffold.Scaffold.route → sansio.app.App.add_url_rule
- sansio.scaffold.Scaffold.delete → sansio.scaffold.Scaffold._method_route → sansio.scaffold.Scaffold.route → sansio.app.App.add_url_rule
- sansio.scaffold.Scaffold.get → sansio.scaffold.Scaffold._method_route → sansio.scaffold.Scaffold.route → sansio.app.App.add_url_rule
- sansio.scaffold.Scaffold.patch → sansio.scaffold.Scaffold._method_route → sansio.scaffold.Scaffold.route → sansio.app.App.add_url_rule

## Tests: graph selected 248 of 370
- `test_basic.test_disallow_string_for_allowed_methods`: executed it directly
- `test_helpers.TestUrlFor.test_url_for_with_scheme_not_external`: executed it directly
- `test_basic.test_url_generation`: executed it directly
- `test_helpers.TestUrlFor.test_url_for_with_alternating_schemes`: executed it directly
- `test_helpers.TestUrlFor.test_url_for_with_anchor`: executed it directly
- `test_helpers.TestUrlFor.test_url_for_with_scheme`: executed it directly

Ran 44 tests: **passed**; 1 failure(s) already present before the patch are ignored

## Suggested reviewers (git history over the blast radius)
- David Lord: 87 commits (20 in the last 2 years), e.g. src/flask/app.py, src/flask/sansio/app.py
- pgjones: 21 commits (1 in the last 2 years), e.g. src/flask/app.py, src/flask/sansio/app.py
- Heisenberg: 1 commits (1 in the last 2 years), e.g. src/flask/sansio/app.py

_Opened by Keel. Every number above is a FalkorDB query result; see queries/*.cypher._
Fixes #5472: Add support for partitioned session cookies

Replays the upstream fix for this issue (commit 9efc1ebe) so the decision pipeline can be evaluated.

## Merge lane: **canary**
Risk score **0.417** (weights: centrality 0.25, reach 0.2, import_reach 0.15, coverage_gap 0.2, churn 0.1, reverts 0.1)

| signal | value | what it measures |
|---|---|---|
| centrality | 0.63 | PageRank percentile of the edited symbols over the call graph |
| reach | 0.482 | transitive callers within 3 hops (log scale) |
| import_reach | 0.957 | share of other source files that import the edited files (4 levels) |
| coverage_gap | 0.0 | share of edited symbols that no test executes |
| churn | 0.2 | commits touching these files in the last year |
| reverts | 0.0 | earlier changes to these files that were reverted |


## What changed
- `src/flask/app.py`: 
- `src/flask/sessions.py`: `sessions.SecureCookieSessionInterface.save_session`

## Why here (localization)
- `src/flask/sessions.py` score 4.399, best symbol `sessions.SecureCookieSessionInterface.save_session` via tests test_session_using_session_settings, test_session_using_samesite_attribute
- `src/flask/json/tag.py` score 3.0, best symbol `json.tag.TaggedJSONSerializer._untag_scan` via tests test_session_using_session_settings, test_session_using_samesite_attribute
- `src/flask/app.py` score 2.625, best symbol `app.Flask.wsgi_app` via tests test_session_using_session_settings, test_session_using_samesite_attribute

## Blast radius of `sessions.SecureCookieSessionInterface.save_session` (5 callers within 3 hops)
- app.Flask.process_response → sessions.SecureCookieSessionInterface.save_session
- testing.FlaskClient.session_transaction → sessions.SecureCookieSessionInterface.save_session
- app.Flask.finalize_request → app.Flask.process_response → sessions.SecureCookieSessionInterface.save_session
- app.Flask.full_dispatch_request → app.Flask.finalize_request → app.Flask.process_response → sessions.SecureCookieSessionInterface.save_session
- app.Flask.handle_exception → app.Flask.finalize_request → app.Flask.process_response → sessions.SecureCookieSessionInterface.save_session

## Tests: graph selected 188 of 366
- `test_basic.test_werkzeug_routing`: executed it directly
- `test_basic.test_endpoint_decorator`: executed it directly
- `test_basic.test_multi_route_class_views`: executed it directly
- `test_testing.test_client_open_environ`: executed it directly
- `test_basic.test_multi_route_rules`: executed it directly
- `test_basic.test_nonascii_pathinfo`: executed it directly

Ran 41 tests: **passed**

## Suggested reviewers (git history over the blast radius)
- David Lord: 89 commits (28 in the last 2 years), e.g. src/flask/app.py, src/flask/sessions.py
- pgjones: 20 commits (7 in the last 2 years), e.g. src/flask/app.py, src/flask/sessions.py
- Maxim G. Ivanov: 1 commits (1 in the last 2 years), e.g. src/flask/app.py

_Opened by Keel. Every number above is a FalkorDB query result; see queries/*.cypher._
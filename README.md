# Keel — an issue-to-PR agent whose merge decision comes from a graph

Keel takes a GitHub issue and a repo, finds where to edit, makes the change, runs the *right* tests, and then
decides **how far to trust its own PR** (`auto_merge` / `canary` / `human_review`), who should review it, and what it
should remember next time. Every one of those decisions is a FalkorDB query; the model never writes Cypher and
never controls the gate.

**Track:** Agents That Act on Connected Data (secondary: Agent Memory — attempts persist in a per-repo memory graph).

## What the graph does (and what happens without it)

| Decision | Graph query / algorithm | Without FalkorDB |
|---|---|---|
| Where to edit | seed symbols (rarity-weighted) → spread over `CALLS` with degree damping → `CO_CHANGED` history → trace through tests that exercise the behaviour (`COVERS`) | lexical retrieval only (`keel/baseline.py`) |
| What to test | tests that executed the edited symbol or any caller ≤2 hops up (`COVERS` × `CALLS`), focused tests first | "same-name file" heuristic |
| How risky | PageRank percentile, transitive callers, coverage gap, churn, reverts — all computed from the graph | no signal |
| Who reviews | `AUTHORED_COMMIT → TOUCHED` over the *blast radius*, recency-weighted | CODEOWNERS guess |
| What to remember | `Attempt` nodes in a separate per-repo memory graph, read before editing | nothing |
| Why | the path (`nodes(path)`) rides along in the PR body | none |

## Results (measured, 12 real pallets/flask issues, replayed at the parent of the real fix)

Cases are public Flask issues + their real fix commits (`data/cases/`). The graph is built from the parent commit
only (code, per-test coverage, git history up to that point). Reproduce: `python -m keel.evaluate --rebuild`.

```
localization (does the real fix's file appear in the top-k?)
  graph     top1 9/12  top3 11/12  top5 12/12
  no-graph  top1 9/12  top3 11/12  top5 12/12
test selection (share of the real fix's test files that the selection reaches)
  graph, everything selected  88%   avg 123 of 367 tests
  graph, first 40 tests only  79%
  same-name heuristic         38%
```

Read this honestly:

* **Localization is a tie.** The no-graph baseline uses the same rarity-weighted lexical signal, and these issues
  mostly name the code they are about. The graph helps on the hardest cases (e.g. #5916: rank 2 vs 4) and hurts on one
  (#5786: rank 5 vs 2), but at n=12 this is not a result.
* **Test selection is where the graph clearly wins** (88% vs 38% of the real fix's test files reached), because
  "which tests exercise this code or its callers" is a graph question and file-name matching cannot answer it. Cost:
  for central symbols the selection is large (up to ~320 tests), so Keel runs a budget of the 40 most focused first
  (79% recall).
* Two cases select **zero** tests (the edited code was not executed by any existing test). Keel says so and refuses to
  `auto_merge` those.
* The 5 earliest cases were used while tuning the retrieval; the other 7 were mined afterwards. Because of a harness
  bug found late (see below) the cleanly held-out claim does not survive, so no split is reported.
* Risk weights and lane thresholds are hand-set (`keel/risk.py`), not fitted. The lane table below shows they behave
  sensibly (a context-passing refactor touching `app.py`/`ctx.py` goes to `human_review`; a one-line `template_filter`
  fix auto-merges) but there is no ground truth to score lanes against.

Lanes from `python scripts/replay_all.py` (the real fix replayed as the patch, tests run, memory accumulating in date order):

```
case     lane          risk  tests selected   title
1af8f957 canary        0.23  0/363            Flask 3.0.1 breaks path-separated command line arguments   (no test executes it)
9efc1ebe canary        0.35  188/366          Add support for partitioned session cookies
28d5a4d7 auto_merge    0.19  3/364            allow setting encoding in open_resource()
c7a53888 auto_merge    0.01  14/365           extend config for Request form memory settings
2c316030 canary        0.38  26/366           dotenv file arg should take precedence over default dotenv files
4995a775 canary        0.48  244/367          Revisit SERVER_NAME's impact on routing and external URL generation
4f7156f2 canary        0.49  245/368          configure and check request.trusted_hosts      (memory recalled 1 earlier attempt)
daf1510a auto_merge    0.01  20/369           template_filter decorator doesn't work without an argument
53b8f082 auto_merge    0.15  192/369          Session is not updated on redirect target endpoint in tests
9822a035 canary        0.38  6/369            stream_with_context does not work with async routes
6a649690 human_review  0.58  295/370          pass context internally instead of using contextvars
e82db2ca canary        0.48  248/370          provide_automatic_options is weird
```

## Demo: the failure path and memory

`python -m keel replay 4995a775 --mode mutant` applies the real fix and then flips one comparison inside
`Flask.create_url_adapter`. The graph-selected tests catch it (`test_app_context_provides_current_app`,
`test_app_tearing_down`, …), the lane is forced to `human_review`, and an `Attempt{outcome: failed_tests}` is written.
`python -m keel replay 4f7156f2` (a different issue touching the same symbol) then **recalls** that attempt, and the
PR says: *"Lane override: memory: an earlier attempt on these symbols failed its tests or was rejected."*

A sample PR body is in `out/` after any replay: it carries the risk table, the call routes of the blast radius, why
each test was selected, who to ask for review, and the memory it used.

## Graph data model

```
(:File {path, kind})-[:CONTAINS]->(:Symbol {qname, name, kind, path, start, end, words, pr})
(:File)-[:CONTAINS_TEST]->(:Test {id, path, words, breadth})
(:Symbol)-[:CALLS {n, w}]->(:Symbol)                 static call graph (imports, re-exports, self./MRO, overrides)
(:Test)-[:COVERS {lines}]->(:Symbol)                 per-test dynamic coverage (coverage.py contexts)
(:Test)-[:STATIC_CALLS]->(:Symbol)
(:File)-[:CO_CHANGED {n}]->(:File)                   git history, bulk commits (>15 files) excluded
(:Person)-[:AUTHORED_COMMIT]->(:Commit {sha, subject, ts, reverted})-[:TOUCHED]->(:File)
(:Person)-[:WORKED_ON {commits}]->(:File)
(:Meta {repo, ref, ref_ts})                          the replay point

memory graph, one per repo/tenant (keel_mem_<repo>):
(:Attempt {id, issue, summary, outcome, lane, at})-[:ABOUT]->(:Sym {qname})
```

One code graph per (repo, commit) and one memory graph per repo, in a single FalkorDB instance (multigraph).

## The Cypher the product depends on

`queries/*.cypher`, one file per tool. Every query is parameterised; the only substitution is a validated integer depth.

| file | tool | notes |
|---|---|---|
| `resolve_symbols` | name check | every tool resolves names here first; unknown names run nothing else |
| `seed_symbols`, `localize_expand`, `cochange`, `test_trace` | `localize` | idf² seeds → damped call-graph spread → co-change → test trace |
| `blast_radius` | `blast_radius` | variable-length `CALLS*1..N`, shortest route kept as evidence |
| `impacted_tests` | `impacted_tests` | `COVERS` of the symbol + of callers ≤N hops, ranked by hops then test breadth |
| `risk_symbols`, `risk_callers`, `risk_tests`, `risk_files` | `risk_score` | PageRank percentile, callers, tests, churn/reverts |
| `reviewers` | `reviewers` | people who changed the blast radius |
| `path_between` | `path_between` | `algo.SPpaths` |
| `load_bearing` | `load_bearing` | `algo.pageRank` |
| `mem_recall`, `mem_remember` | `recall`, memory write | per-repo memory graph |

FalkorDB behaviours that cost time and are worth knowing (also commented in the queries): a variable-length pattern
cannot take a parameter for its bound; `*0..` after `UNWIND` returns nothing; a `MATCH` straight after an aggregating
`WITH` silently returns nothing (add a pass-through `WITH x ORDER BY … LIMIT`); pattern comprehensions are unsupported.

## Guardrails

* **The agent never writes Cypher.** Fixed tool catalogue (`keel/specs.py`), human-written queries, typed arguments.
* **Names are resolved against the graph first.** An unknown symbol/file returns `unknown_symbol` and runs no further
  query (tested, including an injection string). Edits are limited to files that exist in the graph.
* **Four outcomes, never conflated:** answered with path / answered from memory / not in graph / unknown symbol. An empty
  traversal says "not modelled", never "safe".
* **The gate is not in the model's hands.** After `finish`, risk, lane, reviewers and memory are computed from the
  graph. Failing tests, an untested edit, or a failed earlier attempt can only move a PR *toward* review.
* **Model summaries are checked.** A summary naming code that is not in the graph is replaced by a flat one assembled
  from rows.
* **Outward actions are off by default.** Keel writes `out/<case>.md`; it does not push or open PRs.
* **Only newly failing tests gate a PR.** Selected tests are run before and after the patch; failures that already
  exist in the environment are reported and ignored. Tests the PR itself renames or removes are reported as missing.

## Run it

```bash
./scripts/setup.sh                              # venv, Flask checkout, FalkorDB on :6379 (Browser UI on :3000)
. .venv/bin/activate
python -m keel.evaluate --rebuild               # builds 12 graphs (~5 s each), prints the table above
python -m keel replay 4995a775                  # real fix as patch -> tests -> risk -> lane -> reviewers -> PR body
python -m keel replay 4995a775 --mode mutant    # same, but deliberately broken: watch the gate and memory work
python -m keel replay 4995a775 --mode llm       # an LLM localizes, edits and tests through the same tools
#   Anthropic:                 ANTHROPIC_API_KEY=...  (default model claude-sonnet-5-5)
#   any OpenAI-compatible API: put OPENAI_API_KEY / OPENAI_BASE_URL / KEEL_MODEL in .env (copy .env.example; .env is gitignored)
#   (OpenAI, Gemini, Groq, OpenRouter, Together, DeepSeek, Ollama all expose this interface)
python scripts/replay_all.py                    # the lane table
KEEL_CASE=e82db2ca python -m keel mcp           # the graph tools over MCP
python -m pytest tests                          # 14 tests; the integration ones need FalkorDB and built graphs
```

Open http://localhost:3000 and select a graph named `keel_flask_<case>` to see the model.
Pointing Keel at a different Python repo means changing `targets/` and `pipeline.py`'s repo name (single-repo today).

## Limitations

* **`--mode llm` has been run live once**, on Groq `openai/gpt-oss-120b` via the OpenAI-compatible adapter, on issue #5774
  (`stream_with_context` with async routes). What happened, unedited: the first run's patch failed the graph-selected
  tests and was routed to `human_review`; the second run only changed type annotations (it did **not** fix the bug), the
  tests passed, and the lane was still forced to `human_review` because memory held the earlier failed attempt on the
  same symbol. So the gate, test selection and memory behave as designed, but the model's patch quality was poor, and
  passing tests did not mean the issue was fixed (no existing test exercised the bug). **Keel gates how risky a change
  is to merge; it does not verify that the change is correct.** One model, one issue, two runs: no claim about patch
  quality beyond that. The evaluation numbers above come from oracle mode (the real fix as the patch).
* The call graph is static: dynamic dispatch, decorators and `getattr` are invisible, so blast radius can be
  incomplete. Ambiguous attribute calls (>3 candidates) get no edge rather than a wrong one.
* Per-test coverage is function-level, so "covered" means "executed", not "asserted".
* One language (Python), one target repo (Flask), 12 cases. Replays older than ~2024 do not run under current
  dependencies and were dropped.
* The risk score is a transparent weighted sum with hand-set weights; it is unvalidated beyond the sanity check above.
* A bug found late: the graph builder and evaluator could read a worktree left patched by an earlier replay (fix
  leakage). Fixed (`pipeline.checkout` always resets); all reported numbers are from after the fix.

## AI assistance disclosure

Built with Claude Code (Claude Sonnet 5.5) as a coding assistant: it wrote most of the initial code, queries and tests
under direction, and found several of the bugs listed above while measuring. The design (graph model, tool catalogue,
gate rules, evaluation protocol) and its verification are the submitter's responsibility; see the source for the
rationale in comments.

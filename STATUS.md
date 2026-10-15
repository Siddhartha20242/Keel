# Status: PROVEN, WRITTEN, UNKNOWN

PROVEN means I ran it and saw the result below. WRITTEN means the code exists but I have not seen it run. UNKNOWN means I do not know.

## PROVEN (run, with the result I saw)
| Thing | How I ran it | Result |
|---|---|---|
| Graph build for 12 Flask issues | `python -m keel.evaluate --rebuild` | builds, about 5 s per graph |
| Test selection vs "same-named test file" | same command | 88% vs 38% of the real fix's test files reached; 79% when limited to the first 40 tests |
| Localization vs keyword baseline | same command | tie: 9/12 top-1, 11/12 top-3, 12/12 top-5, for both |
| Replay of all 12 fixes with tests and memory | `python scripts/replay_all.py` | lanes: 3 auto_merge, 5 canary, 4 human_review (the four that touch `app.py`/`sansio/app.py`) |
| Failure path | `python -m keel replay 4995a775 --mode mutant` | broken change caught by 42 selected tests, lane human_review |
| Memory | `python -m keel replay 4f7156f2` after the run above | recalled the failed attempt, lane forced to human_review |
| Unit and integration tests | `python -m pytest tests` | 15 pass (integration ones need FalkorDB and built graphs) |
| Live model, one issue | `python -m keel replay 9822a035 --mode llm` on Groq `openai/gpt-oss-120b` | ran end to end twice; run 1 failed tests; run 2 edited type hints only, tests passed, lane human_review from memory |

## Pipeline (GitHub Actions)
| Thing | How I ran it | Result |
|---|---|---|
| `main.yml` (build, run, smoke, push image) | pushed to main | green on GitHub: smoke 11 s, release 19 s |
| `keel-tests.yml` (unit tests, no database) | pushed to main | green on GitHub, 17 s |
| PR analysis on three simulated PRs | `python -m keel.ci analyze` locally against git branches | harmless doc change: 7 tests, auto_merge; broken dedupe: 3 new failures, human_review; new untested file: canary |
| Gate with no model key, a forged "RISK: low" from another user, RISK: high | `python -m keel.gate` with a comments file | no review: human required; forged comment ignored; high: blocked, exit 1 |
| Skeptic and Reporter with no key | `--dry-run` | Skeptic says it could not run and gives `RISK: unknown`; Reporter lists what a human must decide |
| 22 unit tests for the pipeline logic | `python -m pytest tests/test_unit.py tests/test_pipeline_units.py` | pass, no database needed |

| Triage on a realistic issue | `python -m keel.triage --dry-run` locally | recommends "agent can try", points at `store.py`/`codes.py`, 10 of 12 tests, risk 0.277 |
| Fix agent on a realistic issue (dry run, Groq `gpt-oss-120b`) | `python -m keel.fix --dry-run` locally | `localize` found nothing, the agent browsed with `list_symbols`, edited `normalize_url`, 7 selected tests passed, lane auto_merge. The diff is a correct minimal fix. No branch or PR was created |

| Audit ledger: hash chain, tamper detection, graph walk | `python -m pytest tests/test_ledger.py`, `python -m keel.ledger explain` | editing a record or removing one is detected and the chain reports where it breaks; `explain` lists all four actions behind a PR through FalkorDB. Sample data is from locally simulated PRs |
| Co-change clusters (WCC over strong co-change) | on the Flask graph | `app.py` pulls in `blueprints.py`, `helpers.py`, `testing.py`; `json/tag.py` has no cluster. The threshold is a heuristic (35% of the strongest pair, floor 3) |

| Risk with a module-import component | `python scripts/replay_all.py` after adding `import_reach` | evaluation numbers unchanged; lane table shifted. I moved the `human_review` threshold from 0.55 to 0.50 so the central-file changes stay in human review. That is my choice after looking at these 12 replays, not a fitted value |
| Skeptic with a real model (Groq `gpt-oss-120b`), structured findings | `python -m keel.skeptic --dry-run` on two simulated PR contexts | followed the format; on the breaking change it gave RISK high with three findings tied to the three failing tests, but one finding claimed a `KeyError` the diff does not show. On the harmless change it said low |
| Fixer on a real test failure (dry run) | broke `Store.shorten` on a throwaway git worktree, fed the real pytest log to `python -m keel.fix --kind ci --dry-run` | the agent located the symbol, reverted the inverted condition, the 5 graph-selected tests passed, lane auto_merge. No branch or PR was created |
| Import graph | `extract_code` on `demo-app` | `__main__ -> server -> store -> codes`; changing `codes.py` reaches 3 source files |
| **On GitHub:** `pr.yml` on three demo PRs ([#1 docs](https://github.com/Siddhartha20242/Keel/pull/1), [#2 breaking change](https://github.com/Siddhartha20242/Keel/pull/2), [#3 untested new file](https://github.com/Siddhartha20242/Keel/pull/3)) | opened them, watched the runs | tests, image + SBOM, CodeQL, gitleaks and `pip-audit` pass on all three. #2 fails `test`, `build` (the image's smoke test) and `risk-gate` ("blocked: tests the graph selected now fail", 3 new failures out of 5 selected). #1 and #3 pass the gate with "human approval required" because the Skeptic gave no verdict |
| **On GitHub:** the bot's comments | read them on #1 and #2 | one Skeptic comment ("could not run: no model key configured", `RISK: unknown`) and one Reporter note with the evidence table, the blast-radius path, the files that change together and "what a human still has to decide" |
| **On GitHub:** the Skeptic with a live model (Groq `gpt-oss-20b`, set through the `KEEL_MODEL` repo variable) | re-ran PR #1 and pushed a fresh commit to PR #2 | PR #1 (docstring): `RISK: low`, gate "one human approval (RISK: low, graph lane: canary)", Reporter added a model-written summary that passed the name check. PR #2 (broken dedupe): `RISK: high` with a correct finding about `Store.shorten` in `src/shortener/store.py` and a suggested test; gate blocked it. Earlier runs gave `RISK: unknown`: the model was cut off before its verdict line (fixed with a bigger token budget and a one-line retry) |
| **On GitHub:** `fixer.yml`, end to end | PR #2 fails its `test` job | **First real attempt ([draft #5](https://github.com/Siddhartha20242/Keel/pull/5)) was wrong:** the agent edited an unrelated line in `server.py` and the Fixer still said "tests passed", because it only counted *new* failures on a branch that was already red. I closed #5 with a comment. Fixed: a CI fix must now make the tests that failed in CI pass. Two more log-reading bugs surfaced (the `FAILED` lines sit at the end of the log and were cut off; CI prefixes each line with a timestamp). **Second attempt ([draft #6](https://github.com/Siddhartha20242/Keel/pull/6)) is the correct fix**: it restores the inverted condition in `store.py`, and the PR body says "Verified: the 3 tests that failed in CI now pass". That is one issue, one model, one red build: it shows the path works, not how often it succeeds |
| Re-running a workflow does not pick up new code | observed while debugging | `gh run rerun` reuses the original merge commit, so my fixes to `main` did not reach the run until I pushed a new commit to the PR branch (closing and reopening would also have written a bogus "rejected" memory record) |
| **On GitHub:** `issue.yml` | opened [demo issue #4](https://github.com/Siddhartha20242/Keel/issues/4) | triage comment "needs human: nothing in the code graph matches this report" and the label `keel:needs-human`. That is honest, but it is the same vocabulary gap seen locally: the report says "trailing slash", the code says `normalize_url` |
| **On GitHub:** `memory.yml` and the ledger | closing PR #2 (I closed and reopened it to re-run checks) | it recorded PR #2 as `rejected` on the `keel-memory` branch (an accident of my close; the PR is open again). The ledger has 4 records and `python -m keel.ledger verify` says the chain is intact; `explain --pr 2` lists the four actions |
| First real `pr.yml` run | #1 | `analyze` crashed on a variable used before it was set. My earlier local runs predated that edit. Fixed and re-run |

## WRITTEN (code exists, not run)
- Checks on the Fixer's own draft PR: a PR opened with the default token does not start `pr.yml` (PR #6 has no checks). A
  `KEEL_PAT` secret would start them; I have not set one.
- `fix.yml` (the human `keel:fix` label) and the `incident` job: no label was applied and no smoke check has failed. An incident issue opened by the default token
  does not trigger `issue.yml` either.
- That the gate actually blocks a merge: it needs branch protection requiring the `risk-gate` check, which I have not set
  (the repo has none). On PR #2 the gate job failed, but nothing stops the merge button.
- Fork PRs: the agent jobs skip them (no secrets, read-only token). Not tried.
- The Anthropic provider path in `--mode llm`. Only the OpenAI-compatible path ran live; both are covered by a scripted
  fake-client test.
- The MCP server (`python -m keel mcp`): imports cleanly, never connected to a client.
- `./scripts/setup.sh` from a clean machine. I ran its steps by hand on this one.
- Any language other than Python, any repo other than pallets/flask.
- Opening a real pull request from the replay tool: it writes a description to a local `out/` folder (not committed) and stops.

## UNKNOWN
- How often the Fixer picks the right place to edit: the first real attempt (#5) was wrong and the second (#6) was right. I have
  two data points and one model.
- How often the Skeptic's findings are right. It embellishes (see above), so the gate leans on the graph's numbers and a human
  reads the findings; I have no measurement of its precision.
- Whether any of this holds on a repo that is not a small Python app or Flask.
- Whether the Fixer works on a real red build on GitHub, and how it behaves on failures it cannot fix.
- Whether the risk weights and lane thresholds are right. There is no ground truth to score them against.
- Whether the gate actually blocks a merge: it needs branch protection requiring the `risk-gate` check, which I have not set.
- How the ledger and memory behave with many PRs closing at once (the job serializes them, I have not stress tested it).

## Known weaknesses
- The audit ledger is tamper-evident, not tamper-proof: a person with write access to the `keel-memory` branch could rewrite the
  whole chain consistently. Nothing outside the repo anchors the head hash.
- In `memory.yml` the ledger is appended when a PR closes, so a PR that is never closed has no ledger records yet.
- Passing tests do not mean the issue is fixed (see the live run above).
- The call graph is static, so dynamic dispatch is invisible and blast radius can be incomplete.
- Risk weights and lane thresholds are hand-set. There is no ground truth to score lanes against.
- A leak bug (the graph could read a worktree left patched by an earlier replay) was found late and fixed; every number
  here is from after the fix.
- Replays older than about 2024 do not run under current dependencies and were dropped.

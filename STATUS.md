# Status: what has been run and what has not

## Run, with the result I saw
| Thing | How I ran it | Result |
|---|---|---|
| Graph build for 12 Flask issues | `python -m keel.evaluate --rebuild` | builds, about 5 s per graph |
| Test selection vs "same-named test file" | same command | 88% vs 38% of the real fix's test files reached; 79% when limited to the first 40 tests |
| Localization vs keyword baseline | same command | tie: 9/12 top-1, 11/12 top-3, 12/12 top-5, for both |
| Replay of all 12 fixes with tests and memory | `python scripts/replay_all.py` | lanes: 4 auto_merge, 7 canary (one only because no test covers the change), 1 human_review |
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

## Written, but not run
- `issue.yml`, `fix.yml` and the `incident` job on GitHub: they run on issue events and need real issues. Opening a draft PR
  needs the repo setting "Allow GitHub Actions to create pull requests"; a PR opened with the default token does not start
  `pr.yml` unless a `KEEL_PAT` secret is set (a human closing and reopening the draft also starts it). An incident issue
  opened by the default token does not trigger `issue.yml` either.
- `pr.yml` and `memory.yml` on GitHub: they only run on a real pull request, and none has been opened yet. Their steps are
  proven only locally (above). Fork PRs are not supported (no secrets, read-only token): the agent jobs skip them.
- The Skeptic and Reporter with a real model inside Actions. It needs an `OPENAI_API_KEY` repository secret.
- That the gate actually blocks a merge: that needs branch protection requiring the `risk-gate` check, which is a repo
  setting I have not changed.
- CodeQL, gitleaks, the SBOM step and `pip-audit` inside `pr.yml`.
- The Anthropic provider path in `--mode llm`. Only the OpenAI-compatible path ran live; both are covered by a scripted
  fake-client test.
- The MCP server (`python -m keel mcp`): imports cleanly, never connected to a client.
- `./scripts/setup.sh` from a clean machine. I ran its steps by hand on this one.
- Any language other than Python, any repo other than pallets/flask.
- Opening a real pull request from the replay tool: it writes a description to `out/` and stops.

## Known weaknesses
- Passing tests do not mean the issue is fixed (see the live run above).
- The call graph is static, so dynamic dispatch is invisible and blast radius can be incomplete.
- Risk weights and lane thresholds are hand-set. There is no ground truth to score lanes against.
- A leak bug (the graph could read a worktree left patched by an earlier replay) was found late and fixed; every number
  here is from after the fix.
- Replays older than about 2024 do not run under current dependencies and were dropped.

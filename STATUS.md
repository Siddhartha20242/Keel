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

## Written, but not run
- The Anthropic provider path in `--mode llm`. Only the OpenAI-compatible path ran live; both are covered by a scripted
  fake-client test.
- The MCP server (`python -m keel mcp`): imports cleanly, never connected to a client.
- `./scripts/setup.sh` from a clean machine. I ran its steps by hand on this one.
- Any language other than Python, any repo other than pallets/flask.
- Opening a real pull request. Keel writes a description to `out/` and stops; there is no `gh pr create` step.

## Known weaknesses
- Passing tests do not mean the issue is fixed (see the live run above).
- The call graph is static, so dynamic dispatch is invisible and blast radius can be incomplete.
- Risk weights and lane thresholds are hand-set. There is no ground truth to score lanes against.
- A leak bug (the graph could read a worktree left patched by an earlier replay) was found late and fixed; every number
  here is from after the fix.
- Replays older than about 2024 do not run under current dependencies and were dropped.

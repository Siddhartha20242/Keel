# Keel

Keel puts a code graph behind a coding agent, so something other than the model decides how far its change is trusted.
Given a GitHub issue, the agent finds where to edit, makes the change and runs tests. Then queries on a FalkorDB graph
decide whether the change can merge automatically, needs a canary, or needs a human, and who that human should be.
Keel writes a PR description but never pushes, opens or merges anything, so a person still decides what ships.

Track: Agents That Act on Connected Data (secondary: Agent Memory).

Every step leaves something you can open: [out/4f7156f2.md](out/4f7156f2.md) is a replay that recalls an earlier attempt
from memory, [out/9822a035.md](out/9822a035.md) is a live run where a real model wrote the patch, and the queries behind
every number are in [queries/](queries/). STATUS.md separates what I ran from what is only written.

## What I built

Python on FalkorDB. It runs on a laptop with Docker, with no cloud service. GitHub is where the test material comes from:
the issues, fixes and history are from the public pallets/flask repo.

The graph has four layers: a static call graph, per-test coverage (which test ran which function), git history (who
changed what, which files change together, which commits were reverted), and a separate memory graph of every attempt the
agent made. Thirteen Cypher queries read it.

Those queries, not the model, decide where to edit, which tests to run, how risky the change is, who should review it
and whether an earlier attempt on the same code failed. The agent only picks from a fixed set of tools. It never writes
Cypher, and a symbol name it invents returns `unknown_symbol` without running anything. After it finishes, a failing
test, an untested change or a failed earlier attempt can only move the PR toward human review.

Three modes: `oracle` applies the real upstream fix as the patch, `mutant` applies it and then breaks it on purpose, and
`llm` lets a model do the localizing, editing and testing. The tools are also exposed over MCP.

## Results

12 real Flask issues, each replayed at the parent of its real fix:

```
localization (real fix's file in the top-k)    graph 9/11/12   keyword baseline 9/11/12   (tie)
test selection (real fix's test files reached) graph 88%       same-name guess 38%
```

- Test selection is the clear win. Which tests exercise a function or its callers is a graph question.
- Finding where to edit is a tie with a plain keyword baseline.
- A deliberately broken change was caught by the selected tests and sent to human review. The next issue touching the same
  function recalled that failure and was forced to human review too.
- The live mode ran once on Groq `openai/gpt-oss-120b`. Its patch only changed type hints and did not fix the issue, and
  the tests still passed. Keel gates how risky a change is to merge. It does not check that the change is correct.

One language, one repo, 12 issues. The risk weights are hand-set. More in STATUS.md.

## Graph model

```
(:File)-[:CONTAINS]->(:Symbol)    (:Symbol)-[:CALLS]->(:Symbol)    (:Test)-[:COVERS]->(:Symbol)
(:File)-[:CO_CHANGED {n}]->(:File)
(:Person)-[:AUTHORED_COMMIT]->(:Commit {reverted})-[:TOUCHED]->(:File)
memory graph: (:Attempt {outcome, lane})-[:ABOUT]->(:Sym)
```

One code graph per repo and commit, one memory graph per repo, in a single FalkorDB instance.

## Run it

```bash
./scripts/setup.sh                              # venv, Flask checkout, FalkorDB on :6379
. .venv/bin/activate
python -m keel.evaluate --rebuild               # builds the 12 graphs, prints the results above
python -m keel replay 4995a775                  # real fix as the patch -> tests -> risk -> lane -> PR description
python -m keel replay 4995a775 --mode mutant    # same, but deliberately broken
python -m keel replay 4f7156f2                  # then this one recalls the failure
python -m keel replay 9822a035 --mode llm       # a model does the work (keys go in .env, see .env.example)
python -m pytest tests
```

Open http://localhost:3000 and pick a graph named `keel_flask_<case>` to browse it in FalkorDB Browser.

## AI assistance

I built this solo and used an AI coding assistant heavily: Claude Code (Claude Sonnet 5.5). It wrote most of the code, the
queries, the tests and the first draft of this README, working from what I asked for. I used Groq's `gpt-oss-120b` as the
model inside the agent for the live run.

What was mine: the problem and track, the idea of using a graph for the merge decision, Flask and its real issues as test
material, the FalkorDB and API setup, the live runs, and what to report. I asked for the weak results to be written down
as they came out. The assistant also found and fixed several bugs along the way, including one where the graph could see
the fix it was supposed to predict. I am still going through the code so I can explain the graph model, the queries and
the risk score myself.

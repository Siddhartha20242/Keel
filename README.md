# Keel

After AI writes code, the slow and risky steps are review, testing, security and release. Keel puts agents in those steps and
makes a second agent check the first, so a person can see why a change was allowed through. A code graph in FalkorDB sits
behind every decision, and every agent action is stored in that graph, so "why did this ship" is a query, not a log search.

In practice: an issue is triaged against the graph. A person decides whether an agent should try it, or the Fixer reacts when
the unit tests fail. The agent drafts a fix and opens a draft pull request only if the tests the graph selects pass. The pull
request goes through CI, a Skeptic agent that tries to break it and suggests the tests that would catch each problem, a gate
that decides how much human approval it needs, and a Reporter that lists what a person still has to decide. A human merges.
Then the image is built, run, smoke tested and published, and checked again every hour; a failing check opens an incident
issue. Nothing in Keel merges, closes or assigns anything.

Track: Agents That Act on Connected Data (secondary: Agent Memory).

See it run on GitHub: [PR #1](https://github.com/Siddhartha20242/Keel/pull/1) (harmless docs change), [PR #2](https://github.com/Siddhartha20242/Keel/pull/2)
(a "simplification" that breaks the dedupe: the gate blocks it), [PR #3](https://github.com/Siddhartha20242/Keel/pull/3) (new code with no tests)
and [issue #4](https://github.com/Siddhartha20242/Keel/issues/4) (triage). The Skeptic and Reporter comments are on the PRs. With no model key
set in this repo the Skeptic says it could not run; the graph-driven parts (tests, risk, gate, Reporter, ledger) all ran.

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

Three modes of the replay tool: `oracle` applies the real upstream fix as the patch, `mutant` applies it and then breaks it on purpose, and
`llm` lets a model do the localizing, editing and testing. The tools are also exposed over MCP.

## Issue to production, stage by stage

| stage | what runs | who decides |
|---|---|---|
| Issue | `issue.yml`: triage comment with where the graph points, who has changed it, which tests cover it, what depends on it, and whether an agent could try | a person reads it |
| Implement | `fix.yml`: after a person adds the `keel:fix` label, the agent tries a fix and opens a draft PR only if the graph-selected tests pass | a person applied the label |
| Fix a red build | `fixer.yml`: when the unit tests fail on a PR branch, the Fixer reads the log, tries a fix, and opens a draft PR into that branch if the selected tests pass | a person reviews the draft |
| Pull request | `pr.yml`: tests, image and SBOM, CodeQL, gitleaks, `pip-audit`, graph analysis, Skeptic, risk gate, Reporter | a person reviews and merges |
| Release and deploy | `main.yml`: build, run, smoke test, push the image to the container registry | runs after the human merge |
| Monitor | `main.yml` every hour: build from main and smoke test again | |
| Incident | a failing smoke check opens one `keel:incident` issue | a person triages it |
| Memory | `memory.yml`: merged and rejected PRs are recorded on a `keel-memory` branch and recalled by later triage and analysis | |
| Audit | each PR leaves one record per stage (analysis, Skeptic verdict, gate decision, human outcome) in `ledger.jsonl` on that branch, each carrying the hash of the one before | anyone can check it |

Every action is also a graph you can walk. `python -m keel.ledger verify --file data/sample_ledger.jsonl` checks the hash
chain, and `python -m keel.ledger explain --file data/sample_ledger.jsonl --pr 2` loads it into FalkorDB
(`PullRequest -> AgentRun -> Verdict`, with `NEXT` edges for the chain) and lists every action behind that PR. The sample file
comes from three PRs I simulated locally, not from GitHub. A hash chain shows that a record was edited or removed; it does not
stop someone with write access from rewriting the whole chain, so the head hash is printed in the memory job's summary.

### The pull request stage in detail

Keel also runs as a GitHub Actions pipeline around a small app, a URL shortener in `demo-app/` (standard library only, in
memory, so a restart wipes it). The agents can comment but cannot approve, merge or edit, so a human keeps the final say.

On each pull request that touches the app, `.github/workflows/pr.yml` runs the unit tests, builds the container image and an
SBOM, runs CodeQL, gitleaks and `pip-audit`, and smoke tests the image. A job called `analyze` builds the graph from the PR's
base commit, maps the diff onto it, runs the tests the graph selects on base and on head (only failures the PR introduces
count), and scores the risk.

Then three jobs run in order. The Skeptic reads the diff and the graph facts and posts one review: up to four findings, each
naming a file and a test that would catch it, then a final `RISK: low`, `medium` or `high` line. The `risk-gate` job reads that verdict, ignores any comment
that is not from `github-actions[bot]`, and fails only on `RISK: high` or on tests the PR broke. If there is no review it
says so and asks for a human; it does not pass silently. The Reporter posts a status note that quotes both and lists what a
person still has to decide, including files that usually change together with the PR's files but were not touched (weakly connected
components of strong co-change history). When the PR closes, `memory.yml` records whether it merged or was rejected in a JSONL file on a
`keel-memory` branch, and the next analysis recalls it.

After a human merges, `main.yml` builds the image, runs it, smoke tests it, and pushes it to the GitHub container registry.
It also builds and smoke tests it every hour. There is no hosted service: the "live" check is the container started inside
the runner.

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

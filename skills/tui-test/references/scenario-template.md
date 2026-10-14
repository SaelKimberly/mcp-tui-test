# Scenario document template

Copy this skeleton, replace the `<…>` placeholders, and delete the guidance comments. The expected
table goes in **before** the first tool call.

````markdown
---
name: <label>
description: <one sentence an agent can use to decide whether to reuse this scenario>
app: <name> <version>
tags: [<flow>, <flow>]
---

# Scenario: <label>

| Field | Value |
|---|---|
| Run at | <YYYY-MM-DD HH:MM:SS ±HHMM> |
| Launch command | ``env TERM=xterm-256color LC_ALL=C <cmd>`` |
| Session / mode / size | `<label>` / buffer / 120x40 |
| Harness | mcp-tui-test <version from pyproject.toml>, Python <version> |
| Evidence | [<datetime>-scenario-<label>-transcript.md](<...>-transcript.md) |

## Purpose and scope

<What this walkthrough proves. What it explicitly does not cover.>

## Environment and fixtures

<Fixture creation command + initial content; pinned environment variables; anything that could drift.>

## Expected behaviour (written before the run)

| ID | Step | Expected observation |
|---|---|---|
| E1 | launch | <exact text expected on screen> |
| E2 | <keystroke> | <exact state change> |

## Scenario script

Everything below lives in this document's only python block. Pass the whole document to
`run_scenario(script=<this document>, session_id="<label>")`; diagnostics come back at these line
numbers. While iterating, pass just the block as `run_scenario(ad_hoc=<the python block>)`.

```python
launch("env TERM=xterm-256color LC_ALL=C <cmd>", mode="buffer", dimensions="120x40", timeout=30)
expect("<first thing the app paints>", timeout=5)
# ... one step per expectation, in the order the scenario walks them ...
shot(path="/tmp/tui/<label>-1.png")     # colour/contrast/focus evidence — quote the path
resize("80x20")                          # only if the scenario is about a responsive layout
shot(path="/tmp/tui/<label>-2.png")
gif(["/tmp/tui/<label>-1.png", "/tmp/tui/<label>-2.png"], path="/tmp/tui/<label>.gif")
choice({
    "<the outcome the pass expects>": expect("<text that proves it>", timeout=5),
    "<a plausible failure state>": assert_text("<text that reveals it>"),
})
```

## Observed behaviour

| ID | Verdict | Evidence |
|---|---|---|
| E1 | PASS | `<verbatim tool output>` (step 1) |
| E2 | FAIL | `<verbatim tool output>` (step 5) — expected `<...>` |

Verdicts: `PASS` (as expected), `PARTIAL` (expected state reached with a difference worth naming),
`FAIL` (not reached), `BLOCKED` (harness could not observe it — say why). The `choice` match label is
the run-level verdict — quote it.

## Corrections to expectations

<Expectations that were wrong, and the corrected reading. Never rewrite the table above.>

## Visual polish remarks

| # | Where | What | Severity | Recommendation |
|---|---|---|---|---|
| P1 | screen row <N>, col <M> | <what looks wrong> | <blocker/major/minor/nit> | <concrete change> |

## Tooling friction

<Harness limits hit during this run: unobservable states, wrapping/trimming surprises, timings.
Not app findings.>

## Review of findings

<Severity-ordered. For each: evidence → why it matters → recommendation.>

### Verified good

<What the pass actually confirmed works, with the step numbers that prove it.>

### Reproduce

```bash
<one command: the run_scenario invocation>
```
````

## Rules the template encodes

- **Header first.** `name` and `description` are required — the tool rejects a document without them,
  and they are what the index and the next agent search on.
- **One python block.** Exactly one; other fences are documentation. That block is the script.
- **Expected before observed.** The two tables are never merged into one so a reader can see what was
  predicted versus what happened.
- **Verdict column, not prose.** `PASS`/`FAIL`/`PARTIAL`/`BLOCKED` keeps a 12-row table scannable.
- **Evidence cites steps.** `<verbatim output>` (step N) — the transcript holds the full text. A
  colour/contrast/focus remark cites its `shot` path as well.
- **Images are artifacts, not evidence-free.** A `shot`/`gif` step is recorded in the transcript with
  its path, so the picture is reproducible from the run.
- **Polish remarks are about the app.** Table with `Where` = a screen coordinate so the remark can be
  located again in the transcript.
- **A separate *Corrections* section** keeps honesty compatible with a frozen expectation table.
- **The failing attempt survives.** Rerun to a second transcript or append the failed steps; never
  overwrite the evidence that showed the first script was wrong.
- **Filename label == document label == session id.** The label is what you pass as
  `run_scenario(script=<this document>, session_id=<label>)`; a mismatch makes a stray session
  untraceable.

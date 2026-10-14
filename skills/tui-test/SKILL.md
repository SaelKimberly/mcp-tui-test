---
name: tui-test
description: Use when a task involves driving, demoing, walking through, or reviewing a terminal UI application through the tui-test MCP server (launch_tui, send_keys, capture_screen, expect_text, run_scenario), or when linting and executing a Python-like TUI scenario script. Triggers include scenario documents under docs/guides/tui/, expected-vs-actual comparison of TUI flows, visual polish or UX audits of a TUI, regression walkthroughs of ratatui/ncurses/crossterm/textual apps, or any request to verify what a TUI actually shows on screen.
---

# TUI scenario walkthroughs with tui-test

## Overview

The `tui-test` MCP server launches a TUI in a subprocess and exposes its rendered screen as
readable, assertable text — and, for anything the text cannot carry, as a PNG of the same cells. A
walkthrough is only worth anything if a reader can tell the *observed* screen apart from the
*expected* one, so this skill fixes two things: the artifact structure, and the rule that every
observation comes from a tool result.

**Iron rule:** every observation in the scenario document is copied verbatim from a tool result (a
text read, or a screenshot you quote the path of). Anything you reason to but did not see is marked
`[inference]`. A fabricated screen excerpt invalidates the whole review.

## When to use

- The user asks to walk through, demo, verify, or regression-test a TUI's behavior or appearance.
- The user asks for a scenario doc, UX pass, "what does it actually look like", or a polish audit.
- The user reports TUI behavior you must reproduce in a headless session before changing code.

Not for web UIs (use the browser tool) or for unit-testing non-TUI code.

## Deliverables

| Path | Content |
|---|---|
| `docs/guides/tui/<datetime>-scenario-<label>.md` | the scenario **document**: header, expectations, exactly one python block, observed results, polish remarks, review |
| `docs/guides/tui/<datetime>-scenario-<label>-transcript.md` | raw evidence: the report `run_scenario` returns, saved verbatim |
| `docs/guides/tui/INDEX.md` | the collection index — one row per scenario, so the next agent reuses a scenario instead of writing a new one |

- `<datetime>` — local time, `YYYY-MM-DD-HHMMSS` (sortable, filesystem-safe).
- `<label>` — kebab-case flow name, e.g. `nano-save-quit`, `settings-audit`; it is the document's
  `name:` header and the session id you pass to `run_scenario`, so those three must agree.
- Use the same `label` as the session id so a stray session is traceable to its scenario: pass it as
  `run_scenario(script=…, session_id=<label>)`, and keep the filename label, the document label, and
  the session id identical.
- The review is a *section* of the scenario doc. Only write a separate review file if the user asks.

## Workflow

0. **Reuse before you write.** Read `docs/guides/tui/INDEX.md`; if a scenario already covers this
   flow, rerun it instead of authoring a near-duplicate.
1. **Recon.** Read the app's entry point, key bindings, and fixture needs. Pick 4–12 steps that
   cover genuinely distinct flows; a wall of near-duplicate keystrokes proves nothing and buries
   the finding that mattered.
2. **Pre-register expectations.** Fill the *Expected behaviour* table **before the first tool call**.
   This is what makes the walkthrough evidence instead of narration.
3. **Script the steps.** Bare step calls in the document's python block, and — when the run can end in several
   legitimate states — a `choice()` epilogue listing them. That block is the replayable form of the
   scenario, and the thing `run_scenario` lints and executes.
4. **Execute.** `run_scenario(script=<the whole document>, session_id=<label>)`: lint first with
   `dry_run=True`, then run. While iterating on the steps, pass the bare block as
   `ad_hoc=<block>` — no header, no fences — and only then write the document. Lint errors abort before
   anything launches; warnings are advisory, so read them and decide. When the `mcp__tui_test_*` tools
   are not mounted, call the same tool in-process (`references/mounting.md`).
5. **Grade.** Copy observations from the transcript into *Observed behaviour*; verdict
   `PASS` / `FAIL` / `PARTIAL` / `BLOCKED`. Never edit an expectation that turned out wrong —
   keep it and explain the correction in its own section. Keep the failing first attempt too (rerun
   to a second transcript, or paste the failed steps as an appendix): a corrected script with no
   trace of what it corrected is indistinguishable from a script that was never wrong.
6. **Polish remarks.** Only about the app under test (`references/polish-rubric.md`). Harness
   problems belong in *Tooling friction* — mixing them makes an app review that a UI owner cannot act on.
   Any remark about colour, contrast, a colour-only state or focus needs a `shot(path=…)` beside it:
   text reads cannot witness those, and a screenshot's path is a citable observation. Use
   `gif(frames=[…])` when the finding is about a transition over time.
7. **Teardown.** `close_session` for every session, then verify: `list_sessions` → `No active
   sessions`, `pgrep -a <app>` → nothing, rescue/temp files removed (`run_scenario` reports any `*.save`
   written during the run in `rescue_files` and in its report).
8. **Review.** Severity-ordered findings, each with evidence + recommendation, plus a short list of
   what is verified-good (that is what tells the reader the pass had coverage).
9. **Index it.** Add or refresh the scenario's row in `docs/guides/tui/INDEX.md` from its header
   (`name`, `description`, `app`, last verdict), so the next agent can pick it by description:

   ```bash
   { echo "# TUI scenario index"; echo; for f in docs/guides/tui/*-scenario-*.md; do
       name=$(sed -n 's/^name: //p' "$f"); desc=$(sed -n 's/^description: //p' "$f")
       printf -- '- [%s](%s) — %s\n' "$name" "$(basename "$f")" "$desc"; done; } > docs/guides/tui/INDEX.md
   ```

## Scenario document structure

A scenario is a markdown document with a machine-readable header, prose, and **exactly one** fenced
python block (other fences are documentation and are ignored). Follow this order exactly; a reader should be
able to diff two scenarios from different agents.

```markdown
---
name: <label>
description: <one sentence: what this scenario proves, so an agent can pick it by description>
app: <name and version>
tags: [<flow>, <flow>]
---

# Scenario: <label>

| Field | Value |
|---|---|
| Run at | <YYYY-MM-DD HH:MM:SS ±HHMM> |
| App under test | <name, version, exact path> |
| Launch command | `env TERM=xterm-256color LC_ALL=C <cmd>` |
| Session / mode / size | `<label>` / buffer / 120x40 |
| Harness | mcp-tui-test <version>, Python <version> |
| Evidence | [<datetime>-scenario-<label>-transcript.md](<...>-transcript.md) |

## Purpose and scope
What this pass proves, and what it deliberately does not cover.

## Environment and fixtures
How the fixture is created (command + initial content), pinned env vars, anything that could drift.

## Expected behaviour (written before the run)
| ID | Step | Expected observation (exact text where asserted) |
|---|---|---|
| E1 | ... | ... |

## Scenario script
```python
launch("env TERM=xterm-256color LC_ALL=C <cmd>", mode="buffer", dimensions="120x40", timeout=30)
expect("<first thing the app paints>", timeout=5)
# ... one step per expectation, in the order the scenario walks them ...
close()
```

## Observed behaviour
| ID | Verdict | Evidence (verbatim excerpt + transcript step) |
|---|---|---|
| E1 | PASS | ... |

## Corrections to expectations
Expectations that were wrong about the app or the harness, and why.

## Visual polish remarks
App UI only: what looks off, where (row/col), severity, recommendation.

## Tooling friction
Harness limits and surprises hit during this run (see the gotcha table below).

## Review of findings
Severity-ordered. Each finding: evidence → why it matters → recommendation. End with
"Verified good" (what the pass actually confirmed) and "Reproduce" (one command).
```

## Scenario script format

The block is Python-like source made of bare step calls. It is parsed with `ast` and **never
executed**: every argument must be a literal, so what is linted is exactly what runs. Diagnostics from
the block are reported at the document's own line numbers, so a caret points where you wrote it.

```python
launch("env TERM=xterm-256color LC_ALL=C nano notes.txt", mode="buffer", dimensions="120x40", timeout=30)
expect("GNU nano", timeout=5)   # regex — validated at lint time
line(row=1)                     # screen rows are 0-indexed; row 0 is the title bar
send("hello", delay=0.5)        # \n warns on buffer launches; raw-mode Enter is \r
key("down", "down", "enter")    # special keys, checked against the real vocabulary
ctrl("o")
assert_text("hello")
assert_at("hello", row=1, col=0)
region(0, 4)
capture()
stable()
close()
```

A scenario is a *program launcher*, not an inert document: `launch` runs the command you name as
the server's user, with no sandbox. Treat a scenario document from an untrusted source the way you
would treat a shell script.

Rules the linter enforces, so a mistake costs one call instead of a botched run:

- **Bare calls only.** Assignments, imports, `if`/`for`, attribute calls (`app.start()`), f-strings,
  computed arguments, `*args` and `**kwargs` are rejected — they would make the scenario unreplayable.
- **Steps map onto tools**: `launch`→`launch_tui`, `send`→`send_keys`, `ctrl`→`send_ctrl`,
  `key`→`send_special_keys`, `expect`→`expect_text`, `assert_text`→`assert_contains`,
  `assert_at`→`assert_at_position`, `line`→`get_line`, `region`→`get_screen_region`,
  `char`→`get_char`, `cursor`→`get_cursor_position`, `capture`→`capture_screen`,
  `stable`→`wait_for_stable`, `change`→`wait_for_change`, `info`→`get_session_info`,
  `sessions`→`list_sessions`, `shot`→`screenshot`, `gif`→`gif`, `resize`→`resize`,
  `style`→`get_cell_style`, `close`→`close_session`.
- **Arguments are checked against the tool signatures** — unknown names, missing required arguments,
  wrong types, duplicates and bad regexes are errors; the hints name the valid vocabulary
  (`references/tools.md` has the full table). A variadic step (`key`, `gif`) takes either repeated
  positionals or one list literal — `key("up", "down")`, `key(["up", "down"])` and
  `key(keys=["up", "down"])` are the same step.
- **`session_id` is not a script argument.** `run_scenario` applies it to every step; passing one
  inside the script is an error.
- Warnings do not block a run: `\n` in `send` after a buffer launch, buffer-only reads on a
  stream-mode launch, a missing `close()`, a framed capture reused as a `change()` reference.
- **`choice()` is the epilogue.** It may only contain `expect`, `assert_text` and `assert_at`,
  nothing may follow it (the tool then owns teardown, so a trailing `close()` is an error), and only
  one may exist:

  ```python
  choice({
      "saved cleanly": expect("Wrote 1 line", timeout=5),
      "write failed": expect("Error writing", timeout=2),
      "still dirty": assert_text(" *"),
  })
  ```

  Every alternative is evaluated in order and **every** label that matched is reported (several can be
  true at once); the waits add up, so prefer `assert_text` for cheap cases and short `expect` timeouts.
  If nothing matches the step fails — an unexplained screen can never pass as a success. Pair it with
  the *Observed behaviour* table: the matched label is the verdict the run ended on.

`run_scenario` returns the rendered report (lint messages with carets, the resolved pipeline, the
per-step transcript) plus structured fields; the resolved pipeline is what the doc's script block
should converge on, so a reviewed scenario and the executed one cannot drift apart.

## Gotchas (all observed in real sessions)

| Symptom | Cause | Do this |
|---|---|---|
| `launch_tui` prints `✓` but the screen is blank/garbled | The host `TERM` is inherited unless you pass `env=` | Pass `env={"TERM": "xterm-256color"}`, prefix the command (`env TERM=… <app>`), or let `run_scenario` pin it |
| Assertions on labels break on another machine | Host locale is forwarded (Cyrillic/German chrome under `LANG=ru_RU`) | Pin `env={"LC_ALL": "C", "LANG": "C"}`; `run_scenario` pins it by default, a step's `env=` or `pin_env=False` opts out |
| `capture_screen` is missing rows or box-drawing characters | pyte tracks cell diffs imperfectly for full-screen apps | Read with `get_line`, `get_screen_region`, `get_char` |
| `wait_for_change` says "changed" immediately | Its `reference` wants bare buffer text, but `capture_screen` wraps the buffer in a header and a 60-char `=` rule | Slice the wrapper off before reusing a capture as a reference |
| `send_keys "q\r"` does nothing | Raw-mode apps (ratatui/crossterm) want `\r`, not `\n`, for Enter | Send `\r`, or `send_special_keys keys=enter` |
| A buffer read or any key raises `ToolError: Session '…' exited with status N` | The child died (crash, or the app quit itself) and the throw is deliberate — a dead TUI's last frame used to be replayed as if live | Capture the frame *before* the keystroke that quits; otherwise read the app's own log and relaunch. Stream-mode reads are exempt: a finished command's output is still returned |
| A colour/contrast/focus claim cannot be checked | Text reads carry no colour and show no cursor | `style(row, col)` for an assertable fg/bg/attribute read, `shot(path=…)` to look at it; `gif(frames=[…])` for a state transition over time |
| A colour-only state must be pinned in a scenario | Assertions on text cannot express "this row is red" | `style(row, col)` returns `fg=…`/`bg=…`/attributes; a screenshot reports how many cells carry colour (or that the app sends none) |
| A layout that reacts to size is unreachable in a headless run | `dimensions` applies at launch only | `resize("80x20")` mid-session: it delivers SIGWINCH, which is what a responsive layout actually listens to |
| Screenshot shows `.notdef` boxes for a status symbol | No font in the chain covers that glyph | The renderer falls back per glyph (Nerd Font first); point `MCP_TUI_TEST_FONT` at a font that has it |
| A `gif` frame count comes out lower than the frames passed | Pillow drops a frame identical to the previous one | Expected for a static screen; capture frames that actually differ |
| Rescue files (`<fixture>.save`) appear after teardown | `close_session` SIGHUPs the child while the buffer is still modified, so editors write a rescue copy | Write the buffer in-app (Ctrl+O, Enter) before `close_session`; `run_scenario` reports the `*.save` files written during the run (it watches an attached session's own command too), and you delete them during teardown |
| Assertions flake right after a keystroke | Repaint timing | `expect_text` / `wait_for_stable` before reading; `delay` ≥ 0.3 |
| A read right after `send` shows stale text in stream mode | Stream mode's pexpect accumulator only advances on `expect()` | Read through an `expect()` before asserting, or use buffer mode |
| Later `choice` alternatives cannot see text an earlier one matched | In stream mode each `expect()` consumes the accumulator (`W_CHOICE_STREAM_EXPECT`) | Use buffer mode for a `choice` epilogue, or assert cheaply with `assert_text` |
| A scenario document is rejected with `E_NO_PYTHON_BLOCK` / `E_MULTIPLE_PYTHON_BLOCKS` | The tool insists on exactly one fenced python block — that IS the script | Keep one python block; show examples in a `text` or `bash` fence |
| The session is still open after a `run_scenario` call | `keep_alive` defaults to True when attaching (so the final screen can still be captured) and False when `run_scenario` launched the session | Add a `close()` step, pass `keep_alive=False`, or close it yourself |
| A pattern is visibly on screen but `expect_text` misses it | `expect_text` takes a **regex** | Escape metacharacters (`\[`, `\(`) |
| You need the matched text, but `expect_text` answers only `✓ Found expected text: …` | It is a synchronizer, not a reader | Follow it with `get_line` / `get_screen_region` to capture the row you intend to quote |
| "Line 1" is not the file's first line | Rows/columns are 0-indexed **screen** coordinates; row 0 is the title bar | Count from the rendered screen, not from the document |
| Column checks look perfect at 80 columns | Default captures trim trailing whitespace | Use `preserve_format=True` or `get_char` for geometry |

## Common mistakes

| Mistake | Why it hurts | Instead |
|---|---|---|
| Writing the expectation table from the observed screen | Turns the pass into a tautology | Write it before step 4 |
| Reporting paraphrased screen content | Unverifiable, hides truncation and wording bugs | Verbatim excerpt + transcript step number |
| Dumping a 900-line transcript into the doc or the reply | Reader cannot find the finding | Cite step numbers; keep the transcript beside the doc |
| Filing harness bugs as app polish | The app owner cannot act on them | *Tooling friction* section |
| Leaving sessions or rescue files behind | Blocks the next run's session id; pollutes fixtures in shared `/tmp` | Teardown checklist in step 7 |
| One mega-scenario of 40 steps | Runtime grows, findings dilute | 4–12 steps; one flow per step |

## Files

- `references/tools.md` — all 22 tools (including `run_scenario`): arguments, return shape, mode
  support, quirks.
- `references/scenario-template.md` — copy-paste skeleton of the scenario document.
- `references/polish-rubric.md` — what "polish" means for a TUI, with severity guidance.
- `references/mounting.md` — how the server is mounted in omp (tool names), and headless fallback.
- `run_scenario` (a server tool) — lints the script, resolves the pipeline, executes it, and reports
  leftover sessions and rescue files. Quirks and payload fields: `references/tools.md`.

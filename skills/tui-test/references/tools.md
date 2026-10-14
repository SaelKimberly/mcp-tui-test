# tui-test tool reference

Server: `FastMCP("tui-test")`, 18 tools, all synchronous. Seventeen return a human-readable string
(success prefixed `✓`); `run_scenario` returns a `ToolResult` — rendered report as text, machine-readable
fields as structured content. Failures raise `ToolError` with an actionable message — a `ToolError` is a
result to record, not a crash. `session_id` defaults to `"default"` everywhere.

Two modes:

- `buffer` (default) — pyte terminal emulation; position-aware reads and assertions. Use for real TUIs.
- `stream` — pexpect accumulators (`before`/`after`), ANSI-stripped; only line-oriented CLI output.
  Cursor reads return `(-1, -1)`.

## Lifecycle

| Tool | Arguments | Notes |
|---|---|---|
| `launch_tui` | `command`, `session_id="default"`, `timeout=30`, `dimensions="80x24"`, `mode="buffer"` | Spawns via `pexpect.spawn` and sleeps 0.5 s. Re-launching an existing `session_id` closes the old session. Empty `session_id` auto-generates one. `env={"KEY": "value"}` adds or overrides child environment variables (inherited values are kept) — that is how you pin `TERM`/`LC_ALL` instead of shell-prefixing the command. `dimensions` is `WxH` (`Wxh`); an invalid string raises `ToolError`. |
| `list_sessions` | — | One line per session: `id — mode (PID …, WxH, up Ns) — "preview"`. `No active sessions` when empty. |
| `get_session_info` | `session_id` | `command`, `mode`, `dimensions`, `PID`, `uptime`, `Preview`. `Preview: (no capture yet)` until something captures. |
| `close_session` | `session_id` | Kills the child (SIGHUP). Editors may then write `<file>.save` rescue copies. Removing the session is unconditional — it always reports `✓`. |

Sessions live in a module-level dict, so they persist across calls inside one server process, and
they are shared by every caller of that process. Reusing another scenario's `session_id` silently
replaces its session.

## Input

| Tool | Arguments | Notes |
|---|---|---|
| `send_keys` | `keys`, `session_id`, `delay=0.3` (clamped to ≥0.1), `inter_key_delay=0.0` | `keys` is decoded with `unicode_escape`, so write `\r`, `\n`, `\t`, `\x1b`. Raw-mode apps need `\r` for Enter. `inter_key_delay > 0` sends character by character (buffering/animated apps). |
| `send_ctrl` | `key`, `session_id` | Single letter → control character. `"c"` = Ctrl+C (SIGINT), `"d"` = EOF, `"o"`/`"x"` for editor flows. Runs through `ScreenSession.send`, so it settles for 0.3 s and refreshes the buffer like `send_keys`. |
| `send_special_keys` | `keys: list[str]`, `session_id`, `delay=0.3` | Declarative names — no escape guessing. Unknown names raise `ToolError` listing the full vocabulary. Delays once after the whole sequence, so sequences of arrows can outrun a repaint: add `wait_for_stable`/`expect_text` after them. |

Special key names: `up`, `down`, `left`, `right`, `home`, `end`, `page_up`, `page_down`, `backspace`,
`delete`, `insert`, `enter`, `tab`, `escape`, `f1` … `f12`.

After a key step the server refreshes the pyte buffer, but the app may not have repainted yet — read
through an expectation (`expect_text`, `wait_for_stable`, `wait_for_change`) rather than immediately.

## Read

| Tool | Arguments | Notes |
|---|---|---|
| `capture_screen` | `session_id`, `include_ansi=False`, `preserve_format=False` | Whole screen as text. Wrapped: `Screen output (session: …, mode: …):` + 60 `=` + text + 60 `=`. Default strips ANSI and trailing whitespace; `preserve_format=True` keeps padding to full width (and implies no ANSI stripping). `include_ansi` applies to stream mode only. |
| `get_line` | `row`, `session_id` | One screen row; returns `Line N (session: …): …`. Row out of bounds → `ToolError`. |
| `get_screen_region` | `row_start`, `row_end` (exclusive), `col_start=0`, `col_end=None`, `session_id` | Rectangular slice, Python-slice semantics. Most reliable way to inspect one panel; keeps box-drawing characters. |
| `get_char` | `row`, `col`, `session_id` | Single cell; out of bounds returns a space instead of raising. The only tool that answers column geometry unambiguously. |
| `get_cursor_position` | `session_id` | `row N, column M`, 0-indexed; `(-1, -1)` in stream mode. |

All row/column coordinates are **screen** coordinates, 0-indexed, and row 0 is the first rendered
line (usually the app's title bar). They are not document lines.

## Assert and synchronize

| Tool | Arguments | Notes |
|---|---|---|
| `expect_text` | `pattern`, `session_id`, `timeout=10` | Regex over the pyte display; on timeout it falls back to the ANSI-stripped raw pexpect buffer, then raises `ToolError` with a screen dump. Escape metacharacters. This is the primary synchronization primitive. |
| `assert_contains` | `text`, `session_id` | Verbatim substring of the whole screen. |
| `assert_at_position` | `text`, `row`, `col`, `session_id` | Compares `line[col:col+len(text)]` **after stripping whitespace on both sides**, so it validates placement, not exact spacing. |
| `wait_for_stable` | `interval=0.1`, `consecutive=3`, `timeout=10`, `session_id` | Returns the stable screen text (wrapped like a capture). Replaces sleeps after actions that trigger rerenders. |
| `wait_for_change` | `reference`, `interval=0.1`, `timeout=10`, `session_id` | Wait for the screen to differ from `reference`. `reference` is bare buffer text — the framed output of `capture_screen` must be unwrapped first, otherwise the header always counts as a change. Timeout → `ToolError`. |

## Failure modes worth knowing before you write a scenario

- `launch_tui` returns `✓` even when the child immediately dies or renders garbage (see the `TERM`
  row in the gotcha table in `SKILL.md`).
- Nothing reports child liveness: after the app exits, `capture_screen` replays a frozen frame,
  `send_keys` still answers `✓`, `get_session_info` still shows PID/uptime.
- `close_session` sends SIGHUP; editors write `*.save` rescue files next to the fixture.
- Result strings are prose with em dashes and quotes (`list_sessions`), so parse them loosely or not
  at all — prefer assertions on screen content.
- `preserve_format=True` and small `dimensions` interact: a narrow screen truncates real content, which
  is a finding about the app, not about the harness. Test at ≥120x40 unless the scenario is about
  narrow layouts.

## Scenario execution — `run_scenario`

```python
run_scenario(script=None, ad_hoc=None, session_id="default", dry_run=False, stop_on_error=True,
             keep_alive=None, pin_env=True)
```

Two mutually exclusive inputs: `script=` a **markdown scenario document** (a `---` header with required
`name`/`description`, prose, and exactly one python block — other fences are ignored), or `ad_hoc=` a
bare step list with no header, for iterating before the document is written. Passing neither or both is
`E_INPUT_MODE`.

Parses that source with `ast` and resolves it into real tool calls. **The source is never executed**:
every argument must be a literal, so the pipeline that is linted is exactly the pipeline that runs.
Diagnostics from a document's code block are reported at the document's own line numbers.

- **Lint before running.** `dry_run=True` returns the diagnostics plus the resolved pipeline and touches
  no session. Errors abort before anything launches; warnings are advisory.
- **`choice()` epilogue:** the last instruction, and the only place an instruction may appear inside
  another call. `choice({"label": expect(...), "other": assert_text(...)})` — allowed inside: `expect`,
  `assert_text`, `assert_at`. Every alternative is evaluated in written order (their waits add up) and
  every label that matched is reported in `choice.matched`; nothing matches ⇒ the step fails. Nothing
  may follow it (not even `close()`, which is why `W_NO_CLOSE` is suppressed), and a second `choice()`
  is `E_MULTIPLE_CHOICE`.
- **Step → tool:** `launch`→`launch_tui`, `send`→`send_keys`, `ctrl`→`send_ctrl`, `key`→`send_special_keys`,
  `expect`→`expect_text`, `assert_text`→`assert_contains`, `assert_at`→`assert_at_position`, `line`→`get_line`,
  `region`→`get_screen_region`, `char`→`get_char`, `cursor`→`get_cursor_position`, `capture`→`capture_screen`,
  `stable`→`wait_for_stable`, `change`→`wait_for_change`, `info`→`get_session_info`, `sessions`→`list_sessions`,
  `close`→`close_session`.
- **Diagnostics worth knowing:** `E_UNKNOWN_STEP` / `E_ARG_UNKNOWN` carry did-you-mean hints over both step
  and tool names; `E_ARG_MISSING` prints the usage line; `E_ARG_NOT_LITERAL` rejects f-strings, names and
  nested calls; `E_REGEX_INVALID` catches a bad `expect` pattern at lint time; `E_SPECIAL_KEY` lists the real
  key vocabulary; `E_NEGATIVE_POSITION` rejects negative coordinates; `E_SESSION_ARG` rejects a `session_id`
  inside the script; `E_ARG_VALUE` rejects an unknown `mode` or malformed `dimensions`; `E_UNREACHABLE`
  rejects a step after `close()` that is not another `launch`; `E_BAD_SESSION_ID` rejects an empty
  `session_id`. Document-level codes: `E_INPUT_MODE`, `E_MISSING_HEADER`, `E_HEADER_FIELD`,
  `E_HEADER_SYNTAX`, `E_NO_PYTHON_BLOCK`, `E_MULTIPLE_PYTHON_BLOCKS`, `E_UNTERMINATED_BLOCK`. Epilogue
  codes: `E_AFTER_CHOICE`, `E_MULTIPLE_CHOICE`, `E_CHOICE_ARGUMENT`, `E_CHOICE_EMPTY`, `E_CHOICE_LABEL`,
  `E_CHOICE_INSTRUCTION`, `W_CHOICE_STREAM_EXPECT`. Warnings: `W_ENTER_ESCAPE` (`\n` in `send` after a buffer launch), `W_STREAM_MODE_READ`,
  `W_NO_LAUNCH`, `W_LAUNCH_NOT_FIRST`, `W_LAUNCH_REPLACES_SESSION`, `W_NO_CLOSE`,
  `W_CHANGE_REFERENCE_FRAMED`.
- **Session lifecycle:** the script's `launch` step starts a session; `run_scenario` closes it in a
  `finally` when it owns that session. `keep_alive=True` never closes it, `keep_alive=False` closes it
  even when attaching, and the default (`None`) closes only a session this call launched — so an
  attached session survives for a follow-up capture. `launch` after `close()` starts a second app in
  the same scenario. With no live session and no `launch` step the call reports `E_NO_SESSION`, and an
  empty `session_id` reports `E_BAD_SESSION_ID`, instead of running.
- **Environment:** launched children get a pinned `TERM` (when the host's is unset/`dumb`) and
  `LC_ALL`/`LANG`/`LANGUAGE=C`, because inherited values are what make a full-screen app render garbage or
  localize its labels while the harness still says `✓`. A step's `env={...}` overrides; `pin_env=False`
  disables pinning (use it when the scenario is *about* the environment).
- **Failure policy:** `stop_on_error=True` stops at the first failing step; a `ToolError` is recorded as a
  step result, never raised.
- **Structured payload:** `ok`, `action` (`linted` | `dry_run` | `executed`), `session_id`, `attached`,
  `kept_alive`, `header` (the document's parsed frontmatter), `diagnostics`, `pipeline` (a `choice` entry
  carries its `alternatives`), `results`, `session_alive`, `rescue_files`, `choice`
  (`{matched: [labels], evaluated: [{label, step, tool, status: matched|no match|error, elapsed_ms,
  output}], status}`).
- **Blast radius:** the script source is parsed, never executed — but it *is* a program launcher.
  A `launch` step runs an arbitrary command as the server's user, with no sandbox and no shell
  (`launch_tui` execs the command directly; use `sh -c '…'` for shell semantics). Treat a scenario
  document from an untrusted source like a shell script, and never point a CI snippet at a document
  you would not run as a script.
- **Timing bounds:** the linter rejects negative `delay`/`interval` and sub-1 `timeout`/`consecutive`,
  and warns (`W_LONG_BUDGET`) above 300 s, because one step can otherwise block the tool call.
- **Two epilogue cautions:** on a stream-mode session an `expect()` inside `choice` consumes the pexpect
  accumulator, so a later alternative cannot see text an earlier one matched (`W_CHOICE_STREAM_EXPECT` —
  use buffer mode); and every alternative is evaluated, so their timeouts add up.
- **Teardown debris:** `rescue_files` lists `*.save` copies an editor wrote when the child was SIGHUP'd
  with a modified buffer; watched directories come from the scenario's launch commands plus the attached
  session's own command. Determining them is read-only (resolve + `glob("*.save")`) and happens before
  the first step, so a script can name a directory the tool will list.

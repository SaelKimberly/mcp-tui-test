# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.1.0] - 2026-10-16

### Fixed
- **Terminal queries are answered**: a buffer-mode session bound `screen.write_process_input` to the
  library's no-op default, so the replies to what an app asks the terminal (`ESC[6n` cursor position,
  `ESC[c` device attributes) were dropped. A crossterm-based app blocks on that reply and, unanswered,
  exits before drawing — `xrat tui` died with `error: application I/O failed`.
- **Queries are answered in time**: `launch_tui` settled with a bare `time.sleep(0.5)` and never
  drained, so the reply went out only when the *next* tool call arrived. Past an app's ~2 s wait
  (again `xrat tui`) the child was already gone. The settle window now drains the buffer.


## [1.0.1] - 2026-10-14

### Fixed
- **Crash at import on a fresh install**: `from fastmcp.tools.tool import ToolResult` raised
  `ModuleNotFoundError` as soon as the server started, because FastMCP 4 moved `ToolResult` into
  `fastmcp.tools.base` (it is also re-exported from `fastmcp.tools`). `uvx mcp-tui-test` resolved
  `fastmcp>=3.4.2` to 4.1.0 and died before serving a single request.
- The dependency floor is now `fastmcp>=4.1.0` (a major bump), `uv.lock` is re-resolved against
  fastmcp 4.1.0 / mcp 2.3.0, and the whole suite (272 tests) passes against it.


## [1.0.0] - 2026-10-14

### Added
- **`get_cell_style` tool + `style` step**: per-cell fg/bg (16-colour name, 6-hex for 256/true colour,
  or `default`) and bold/reverse/italic/underline/strike/blink — the text-form witness that makes a
  colour-only state assertable in a scenario instead of only visible in a screenshot.
- **Full attribute rendering**: the PNG now honours italic, underline and strikethrough (bold and
  reverse were already drawn) and paints colour emoji from the colour-emoji font (rendered once at
  its 109px bitmap strike and scaled into the cell) when one is installed.
- **Configurable canvas colours**: `MCP_TUI_TEST_FG` / `MCP_TUI_TEST_BG` set the default fg/bg, so a
  light-theme terminal does not get a black-canvas screenshot.
- **Colour fixture**: `examples/color_tui_app.py` — a static screen with ANSI, 256-colour,
  true-colour, background, bold, underline, strike, italic and reverse cells, used by the integration
  colour tests.
- **`run_scenario` tool**: lint and run a TUI scenario written as bare step calls (`launch`, `send`,
  `expect`, `assert_text`, `line`, …). Parsed with `ast` and never executed — every argument must be
  a literal, so the linted pipeline is exactly what runs. Returns a rendered report plus structured
  fields (`ok`, `header`, `diagnostics`, `pipeline`, `results`, `choice`, `rescue_files`).
- **`choice()` epilogue**: the last instruction may state which of several assertion outcomes are
  true, e.g. `choice({"saved cleanly": expect("Wrote 1 line"), "still dirty": assert_text(" *")})`.
  Every alternative is evaluated and every matching label is reported; if nothing matches, the step
  fails so an unexplained screen cannot pass as a success.
- **Markdown scenario documents**: `script=` accepts a document with a `name`/`description` header
  and exactly one fenced python block (other fences are ignored, diagnostics point at the document's
  own lines); `ad_hoc=` accepts a bare step list for iteration before the document exists.
- **`launch_tui(env=...)`**: add or override child environment variables, so `TERM`/`LC_ALL` can be
  pinned per launch instead of shell-prefixing the command.
- **Agent skill** (`skills/tui-test/`) covering the scenario workflow (reusable documents, an
  expectations-first walkthrough, a visual polish rubric, a findings review, `docs/guides/tui/INDEX.md`),
  plus a project-scoped `.omp/mcp.json` mount for oh-my-pi sessions.

- **`screenshot` tool**: render the current buffer-mode screen to a PNG from the same pyte cell grid
  the text tools read — colour, bold, reverse video, the cursor as an inverted block, and a per-glyph
  font fallback (a Nerd Font is preferred; `MCP_TUI_TEST_FONT` overrides it) so emoji and status
  symbols are drawn instead of `.notdef` boxes. This is the only witness of colour, contrast and focus.
- **Palette note on `screenshot`**: the result states how many cells carry colour, or that the app
  emits none — so a monochrome picture cannot be mistaken for a renderer dropping colour.
- **`gif` tool**: stitch `screenshot` PNGs into an animated GIF (frames resized onto the first frame's
  canvas). Takes no `session_id`.
- **`resize` tool**: resize a live session's pty (SIGWINCH reaches the child) *and* the emulated screen,
  so a responsive layout can be driven mid-session — including the branch a shrink reaches that a fresh
  small launch does not.
- **Scenario steps** `shot` → `screenshot`, `gif` → `gif`, `resize` → `resize`, with argument
  validation, a `W_STREAM_MODE_READ` warning for `shot` on a stream session, and `gif` exempt from
  session-id injection.
- **Pillow dependency** for raster rendering; it is imported lazily, so the text tools keep working
  (and the visual tools raise a `ToolError` naming the install) when it is absent.
- **Skill**: `references/tools.md` documents the three new tools and the corrected liveness behaviour;
  the gotcha table, polish rubric and scenario template now say when a screenshot is required evidence.

### Changed
- **BREAKING: a dead child is reported instead of silently replayed.** On a buffer-mode session, reads
  (`capture_screen`, `get_line`, `get_screen_region`, `get_char`, `get_cell_style`, `get_cursor_position`,
  `expect_text`, `screenshot`) and every write (`send_keys`, `send_special_keys`, `send_ctrl`, `resize`)
  raise `ToolError` naming the exit status once the process has exited; previously they answered `✓`
  from the last frame, so a walkthrough could pass against a dead app. Stream-mode reads are exempt —
  a finished command's accumulated output is still returned.
- **BREAKING: buffer rows are column-exact.** `capture_screen` / `get_line` / `get_screen_region` /
  `get_char` render one character per cell (a wide glyph plus a filler space), so a cell's index is its
  column. Text reads of emoji rows therefore contain one space more than before, and comparisons built
  against the old pyte `display` shape will differ.
- **BREAKING: `run_scenario` argument checking tightened.** A positional list literal (`key(["up",
  "down"])`, `gif([...])`) is now accepted where it was previously rejected as a type error.
- **`launch_tui` `mode` default is `buffer`**; the README previously documented `stream`.

### Fixed
- **Positional list literals for variadic steps**: `key(["up", "down"])` and `gif(["/a.png", "/b.png"])`
  were rejected with `E_ARG_TYPE` while the keyword form accepted the same list; both spellings now
  resolve to one step.
- **pyte `Screen.display` crash on emoji/TUI screens**: a stale wide-character stub cell (an emoji flag
  drawn, then repainted over) made pyte's own renderer raise `IndexError: string index out of range`,
  killing reads at random. All reads now go through `screen_lines()`, which renders the cell grid
  directly and cannot crash on a stub.
- **Wide-character column drift**: rendered rows are now exactly `columns` characters — a wide glyph
  occupies its own column and a filler space the next — so `get_char`, `assert_at_position` and region
  slices are column-exact right of an emoji, which they were not before.
- **Dead children no longer impersonate a live session**: on a buffer-mode session, `capture_screen`,
  the line/region/cursor reads, `expect_text` and `screenshot` raise a `ToolError` naming the exit
  status once the child has exited, instead of replaying the frame it died on and answering `✓`.
  Writes (`send_keys`, `send_special_keys`, `send_ctrl`, `resize`) raise in both modes. Stream-mode
  reads are deliberately exempt: a finished command's accumulated output is a record, and reading it
  is the reason stream mode exists.
- **`launch_tui` terminal size**: out-of-range `dimensions` are rejected with a clear error instead of
  failing deep inside pexpect, which could block the call for tens of seconds.
- **Session cleanup**: a failed `launch` step no longer closes an attached session it did not create,
  and two concurrent runs on one session id no longer raise.
- **Polling overshoot**: `wait_for_stable` / `wait_for_change` no longer sleep past their deadline.
- **`launch_tui` docstring**: documents that the command is exec'd without a shell, so shell
  metacharacters are passed through literally (`sh -c '…'` when a shell is needed).

## [0.3.0] - 2026-06-19

### Added
- **Robust buffer draining**: `_update_buffer()` now uses multi-pass drain (3 passes with
  0.1s gaps) to catch staggered/batched ANSI output from ratatui apps
- **Raw pexpect fallback**: `expect_text()` buffer mode falls back to checking the raw
  pexpect buffer (ANSI-stripped) when pyte doesn't track the pattern — catches cases
  where pyte misinterprets alternate-screen or cell-diff sequences
- **Integration test for ratatui**: `TestRatatuiApp` exercises a real crossterm/ratatui
  binary with buffer mode, tab switching, and Enter key handling
- **Probe script**: `examples/test_xray_tui.py` documents the diagnostic workflow used
  to validate the fixes
- **`send_special_keys` tool**: Send special keys by name (up, down, enter, escape, f1-f12, etc.) without raw escape sequences. Accepts a list of key names and maps each to the corresponding terminal control sequence.

### Changed
- **`send_keys` delay enforcement**: Minimum delay of 0.1s is now enforced with
  `max(delay, 0.1)` instead of being docstring-only
- **`send_keys` docstring**: Updated to clarify that crossterm apps expect `\\r` (CR)
  for Enter, not `\\n` (LF); `\\n` does not trigger Enter in raw mode

- **All 12 tool docstrings**: Rewritten for conciseness and scannability — shorter first-line summaries, bolded mode distinctions, tighter caveats, consistent Args/Returns/Raises. Non-functional change.
### Fixed
- **Buffer-mode `expect_text` timeout**: When pyte doesn't track a pattern (e.g. complex
  ratatui ANSI sequences), the fallback to raw pexpect output prevents spurious timeouts
## [0.2.0] - 2025-01-11

### Added
- **Dual Testing Modes**: Stream mode (pexpect) and Buffer mode (pexpect + pyte)
- **ScreenSession wrapper class**: Combines pexpect and pyte for hybrid testing
- **Buffer mode tools**:
  - `assert_at_position()`: Assert text appears at specific screen coordinates
  - `get_cursor_position()`: Track cursor position in real-time
  - `get_screen_region()`: Extract rectangular regions of the screen
  - `get_line()`: Get specific lines from the screen buffer
- **Enhanced existing tools**:
  - `launch_tui()`: Added `mode` parameter to choose stream or buffer mode
  - `capture_screen()`: Added `use_buffer` parameter for mode override
  - `assert_contains()`: Added `use_buffer` parameter for mode override
  - `list_sessions()`: Now shows mode for each session

### Changed
- **pyte dependency**: Added pyte>=0.8.0 for screen buffer emulation
- **Performance**: ANSI escape regex compiled once for better performance
- **Session management**: Sessions now store ScreenSession objects instead of raw pexpect.spawn
- **Documentation**: Comprehensive updates to README.md and EXAMPLES.md
  - Added "Testing Modes" section explaining when to use each mode
  - Documented all new buffer-mode tools
  - Added 12+ new examples covering both modes
  - Updated technical details section

### Improved
- **Screen buffer awareness**: Buffer mode provides accurate screen state representation
- **Position-based testing**: Can now verify text at specific row/column coordinates
- **Cursor tracking**: Monitor cursor movement in TUI applications
- **Region extraction**: Extract and compare specific screen regions
- **Better TUI support**: Full support for ncurses-based applications

## [0.1.0] - 2025-01-11

### Added
- Initial release of MCP TUI Test server
- **Core functionality**:
  - `launch_tui()`: Launch TUI applications for testing
  - `send_keys()`: Send keyboard input
  - `send_ctrl()`: Send Ctrl+Key combinations
  - `capture_screen()`: Capture screen output
  - `expect_text()`: Wait for specific text to appear
  - `assert_contains()`: Assert text is present
  - `close_session()`: Close testing sessions
  - `list_sessions()`: List active sessions
- **Session management**: Support for multiple concurrent TUI sessions
- **Example application**: Simple interactive TUI app for testing
- **Documentation**: README.md and EXAMPLES.md with usage examples
- **FastMCP integration**: Built on FastMCP framework
- **pexpect integration**: Terminal process control and I/O

### Technical
- Python 3.10+ support
- FastMCP server implementation
- pexpect for process spawning and control
- Basic ANSI escape code stripping

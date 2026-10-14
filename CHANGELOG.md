# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Added
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

### Fixed
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

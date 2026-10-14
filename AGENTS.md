# Repository Guidelines

## Project Overview

`mcp-tui-test` is a Python MCP (Model Context Protocol) server for testing Terminal User Interface applications programmatically. "Like Playwright, but for TUIs." It launches a TUI app in a subprocess, feeds input, captures screen output — all via 22 MCP tools callable by AI agents. Supports two modes: **stream** (pexpect, for CLI tools) and **buffer** (pyte terminal emulation, for full TUIs with position-aware assertions). Buffer mode can also render the screen to a PNG (and stitch frames into a GIF), which is the only way to witness colour, contrast and focus.

Version 1.0.0 (see `CHANGELOG.md` for the breaking changes that motivated the major bump). MIT license. Four-module core (~3,380 lines: `_server`, `_scenario`, `_render`, `_screen`).

## Architecture & Data Flow
```
Agent → FastMCP → @mcp.tool → require_session() → ScreenSession → ScreenModel → string response
                                                    ↑
                                              sessions dict
```

**Lifecycle:**
1. `launch_tui(command, mode="buffer")` → `pexpect.spawn()` → `ScreenSession` → stored in `sessions[session_id]`
2. Tools retrieve session via `require_session(session_id)`, operate on `ScreenSession` methods
3. `close_session()` kills the subprocess, removes from `sessions`
4. `list_sessions()` dumps active sessions with metadata
| Backend | pexpect accumulators (`before`/`after`) | pexpect → pyte `Screen`/`Stream` |
| `capture()` | `process.before` + `process.after`, ANSI-stripped | `screen_lines()` over the pyte buffer |
| `expect_text()` | `pexpect.expect()` (regex-native) | `re.search()` polling `screen_lines()` |
| Cursor | `(-1, -1)` always | pyte cursor position |
| Read tools | N/A | `get_line`, `get_screen_region`, `assert_at_position`, `get_cursor_position`, `get_char` |
| Visual tools | N/A | `screenshot` (PNG), `gif` (frames → animation) — `_render.py` |
| Style reads | N/A | `get_cell_style` — fg/bg + bold/reverse/italic/underline/strike per cell |
| Liveness | not checked | `assert_alive()` raises on a dead child for every read and write |

**Key types:**

- `CursorPosition(row, col)` — frozen+slots dataclass
- `ScreenModel(lines, cursor, width, height, mode)` — frozen+slots dataclass; `.text` property joins lines
- `ScreenSession` — wraps pexpect + pyte; methods: `send()`, `capture()`, `_update_buffer()`, `assert_alive()`, `resize()`, `close()`; fields: `launch_time`, `command`, `session_id`, `_last_preview`
- `sessions: dict[str, ScreenSession]` — module-level mutable state
- `require_session(id)` — lookup or `ToolError`
- `SPECIAL_KEYS: dict[str, str]` — maps key names (e.g. "up", "f1") to terminal escape sequences

**Helper module (`_screen.py`):**

- `ANSI_ESCAPE` regex + `strip_ansi(text)` — ANSI escape removal
- `screen_lines(screen)` — the crash-safe screen renderer: one string per row, exactly `columns` characters, so a cell's string index **is** its column. Replaces `pyte.Screen.display`, which raises `IndexError` on a stale wide-char stub (an emoji flag drawn then repainted over)
- `drain_buffer(process, screen, stream, max_passes=3, max_reads=100)` — multi-pass pyte drain with safety cap to prevent indefinite hang (extracted from `ScreenSession._update_buffer`)
- `screen_text_changed(a, b, threshold=0.05)` — line-aware diff for polling
- `poll_until_stable(session, timeout, interval, consecutive)` — wait for screen to stop changing
- `poll_until_changed(session, reference, timeout, interval)` — wait for screen to differ from reference

**Raster module (`_render.py`):**

- `render_screen_png(screen, path, font_size=14, font_path=None, show_cursor=True)` — draws the pyte cell grid with Pillow: fg/bg, bold, reverse, italic, underline, strikethrough, the cursor as an inverted block, and colour emoji as scaled tiles from the colour-emoji font
- `default_colors()` — canvas/default fg+bg, overridable with `MCP_TUI_TEST_FG` / `MCP_TUI_TEST_BG`
- `frames_to_gif(frames, path, duration_ms=500, loop=0)` — stitches screenshots into an animation
- `describe_screen_attributes(screen)` — how many cells carry colour, or that the app emits none,
  which `screenshot` reports so a monochrome picture is not read as a renderer bug
- `_GlyphFonts` — per-character font fallback chosen by *measured* coverage (render the char, compare against the unassigned-code-point mask); a Nerd Font is preferred as primary, overridable with `MCP_TUI_TEST_FONT`
- Pillow is imported lazily: the text tools keep working without it, and the visual tools raise a `ToolError` that names the install

**Buffer drain:** `_update_buffer()` delegates to `drain_buffer()` in `_screen.py` — 3 drain passes (timeout=0, 0.1s gap, max 100 reads per pass) via `process.read_nonblocking()`. Falls back to raw pexpect buffer (ANSI-stripped via `strip_ansi`) in `expect_text` if pyte doesn't track the pattern.

## Key Directories

```
./
├── src/mcp_tui_test/       # Package root
│   ├── _screen.py          # Screen polling/detection helpers + screen_lines() renderer
│   ├── _render.py          # Pillow raster rendering: PNG screenshots, GIF stitching, font chain
│   ├── _scenario.py        # Scenario script parsing/lint/pipeline (pure: no session, no subprocess)
│   ├── _server.py          # Dataclasses, ScreenSession, 22 MCP tools (incl. run_scenario)
│   ├── __init__.py          # Exports `mcp` FastMCP instance
│   └── __main__.py          # `python -m mcp_tui_test`
├── tests/
│   ├── conftest.py          # DummyProcess, session/buffer_session fixtures
│   ├── unit/                # 212 tests, no subprocesses
│   │   ├── test_screen.py
│   │   ├── test_screen_session.py
│   │   ├── test_capture.py
│   │   ├── test_scenario.py
│   │   ├── test_visual.py
│   │   └── test_errors.py
│   └── integration/         # 66 tests, real subprocess via mcp.call_tool()
│       └── test_tools.py
├── examples/
│   ├── example_tui_app.py   # Simple print/input menu (not a real TUI)
│   ├── color_tui_app.py     # Static colour/attribute fixture (ANSI, 256, truecolour, styles)
│   └── test_xray_tui.py     # Probe script for ratatui app diagnosis
├── docs/                    # MkDocs website source
│   ├── index.md             # Doc page (lags code)
│   ├── examples.md          # Doc page (lags code)
│   └── stylesheets/
├── skills/tui-test/         # Agent skill: TUI scenario walkthrough workflow
│   ├── SKILL.md             # Workflow, doc structure, gotcha table
│   └── references/          # tools.md, scenario-template.md, polish-rubric.md, mounting.md
├── thirdparty/tmux-shot/    # Local reference checkout (git-ignored): tmux + Pillow PNG/GIF capture
├── .omp/mcp.json            # oh-my-pi project mount of this repo's own MCP server
└── EXAMPLES.md              # 15 worked examples
```

## Development Commands

All commands use `uv` (Python ≥3.14 required).

```bash
# Setup
uv sync                           # Install all deps (runtime + dev)

# Run the server
uv run mcp-tui-test               # Console script entry point
uv run python -m mcp_tui_test     # Alternative

# Tests
uv run pytest -m "not integration" -v    # Unit tests only (fast, 212 tests)
uv run pytest -v                         # Full suite (unit + integration, ~60s)
uv run pytest tests/unit/test_screen.py -v
uv run pytest tests/unit/test_screen_session.py -v
uv run pytest tests/integration/test_tools.py -v -k "buffer"

# Smoke test the tools
uv run python -c "
import asyncio
from mcp_tui_test._server import mcp
async def s():
    r = await mcp.call_tool('launch_tui', {'command': 'python examples/example_tui_app.py'})
    print(r)
    await mcp.call_tool('close_session', {})
asyncio.run(s())
"
```

No linting, formatting, or type-checking commands in dev deps. Ruff config exists in pyproject.toml (line-length=120, target-version=py314, preview enabled) but ruff itself is not declared as a dev dependency.

### Module structure
- `_server.py` contains dataclasses, `ScreenSession`, the sessions dict, and 18 `@mcp.tool` functions (including `run_scenario`); `_screen.py` holds extracted helpers (ANSI stripping, drain_buffer, polling logic); `_scenario.py` is a pure scenario-script parser/linter (ast only, never executes the source) that `run_scenario` consumes
- Tool functions are module-level, decorated with `@mcp.tool`, take plain params (not dependency-injected)
- No classes or tool registrations outside `_server.py`

### Error handling
- **Expected failures**: raise `ToolError("message")` from `fastmcp.exceptions` — FastMCP surfaces these as clean MCP errors
- **Unexpected errors**: let raw exceptions propagate — FastMCP wraps them into ToolError with traceback
- Format: `raise ToolError(f"Assertion failed: '{text}' not found in output")`
- Session lookup failure → `ToolError("No active session found: '{session_id}'")`

### Session management
- Global `sessions: dict[str, ScreenSession]` — single source of truth
- `launch_tui` replaces existing session with same `session_id` (closes old, creates new)
- `close_session` deletes from dict after closing process
- Tools call `require_session(id)` at entry — raises `ToolError` on miss
- Reads and writes call `session.assert_alive()` — a dead child raises `ToolError` (exit status named) instead of replaying a frozen frame as if live

### MCP tool signature patterns
- All tools accept `session_id: str = "default"`
- Return a human-readable string with `✓` prefix on success
- Buffer-only tools (`assert_at_position`, `get_line`, `get_screen_region`, `get_cursor_position`) don't check mode — they call `session.capture()` and work from ScreenModel; stream-mode sessions return meaningless data gracefully. `screenshot` is the exception: it *does* check, because a stream session has no cell grid to draw
- Timing params (`delay`, `timeout`) have sensible defaults (0.3s, 10s)

### Dataclass patterns
- `@dataclass(frozen=True, slots=True)` for CursorPosition and ScreenModel
- Immutability enforced by `frozen`; `slots` reduces memory
- ScreenModel has a computed `.text` property (never stored)

### Testing patterns
- Unit tests use `DummyProcess` test double (hand-rolled, mimics pexpect.spawn surface)
- Integration tests use `mcp.call_tool()` async, launch real subprocess
- `fresh_sessions` fixture saves/restores global sessions dict around tests
- Test classes named `Test*`, integration classes have `@pytest.mark.integration @pytest.mark.asyncio`
- Docstrings on every test describing what's being tested

## Important Files

| File | Role |
|---|---|
| `src/mcp_tui_test/_screen.py` | Extracted helpers: ANSI stripping, drain_buffer, screen polling |
| `src/mcp_tui_test/_render.py` | Pillow raster rendering: `render_screen_png`, `frames_to_gif`, per-glyph font chain |
| `src/mcp_tui_test/_server.py` | Dataclasses, ScreenSession, 22 tools incl. `run_scenario` |
| `src/mcp_tui_test/_scenario.py` | Scenario script (DSL) parser, lint diagnostics, pipeline resolution, report rendering |
| `src/mcp_tui_test/__init__.py` | Exports `mcp` |
| `src/mcp_tui_test/__main__.py` | Entry point: `mcp.run()` |
| `pyproject.toml` | Build config, deps, scripts, pytest settings |
| `uv.lock` | Lockfile (revision 3), all transitive deps pinned |
| `tests/conftest.py` | DummyProcess + fixtures |
| `tests/unit/test_screen.py` | Unit tests for _screen.py helpers |
| `tests/unit/test_screen_session.py` | Unit tests for ScreenSession |
| `tests/integration/test_tools.py` | Integration tests for the tools, incl. screenshot/gif/resize |
| `examples/example_tui_app.py` | Simple print/input menu app for testing |
| `examples/test_xray_tui.py` | Probe script for ratatui app diagnosis |
| `docs/index.md` | MkDocs version of README |

## Runtime/Tooling Preferences

- **Runtime**: Python ≥3.14
- **Package manager**: `uv` (uv sync, uv run, uv add, uv lock)
- **Build backend**: setuptools (`setuptools>=61.0`)
- **MCP framework**: FastMCP (`>=3.4.2`) — tools are registered via `@mcp.tool` decorator on sync functions
- **Process control**: pexpect (`>=4.9.0`)
- **Terminal emulation**: pyte (`>=0.8.2`)
- **Formatters/linters**: Ruff config (`[tool.ruff]`) in pyproject.toml, but ruff not in dev deps; can be run via `uvx ruff` or installed ad-hoc.
- **Documentation site**: MkDocs with cinder theme, mermaid2 plugin, localsearch plugin
- **Claude Desktop config**: `claude_desktop_config.example.json` — command is `mcp-tui-test` (console script)

## Testing & QA

### Framework
- **pytest** ≥9.1.0 with **pytest-asyncio** ≥1.4
- `asyncio_mode = "auto"` — async test functions work without decorators
- Custom marker: `integration` — deselect with `-m "not integration"`

### Test boundaries
- **Unit tests** (no marker): 6 files, 212 tests (`test_scenario.py` covers the scenario parser/linter, `test_visual.py` the renderer, both without a subprocess). Use `DummyProcess` to mock pexpect. Cover ScreenSession init/send/capture/close/resize/liveness, ScreenModel/CursorPosition immutability, require_session error handling, `_screen.py` helpers (screen_lines, screen_text_changed, poll_until_stable, poll_until_changed, drain_buffer), and PNG/GIF rendering. Fast (~3.5s).
- **Integration tests** (`@pytest.mark.integration`): 1 file, 66 tests (60 run without the ratatui fixture; 6 skip unless it exists). Launch real subprocess via `mcp.call_tool()`. Cover the tools across launch/expect/send/assert/buffer-mode/Ctrl-key/special-keys/screenshot/gif/resize/`run_scenario` scenarios, plus 6 ratatui tests that skip unless the fixture binary exists at `/tmp/ratatui-test-app/target/release/ratatui-test-app`. Slower (~60s).

### What's tested vs gaps
- ✅ ScreenSession init, send, capture (stream+buffer), close
- ✅ ScreenModel/CursorPosition immutability, text property
- ✅ require_session lookup/error
- ✅ All 22 MCP tools at integration level (smoke); colour is covered by `examples/color_tui_app.py`
- ✅ `run_scenario` lint classes, dry run, attach/keep_alive, env pinning, rescue-file reporting
- ✅ `launch_tui` `env` parameter reaching the child process
- ✅ Review regressions: invalid `mode`/`dimensions` rejected at lint time, empty variadic list, non-string
  dict keys, `launch` after `close`, timing bounds, surrogate/control-character input, rescue-dir
  resolution (relative, quoted, missing leaf), attach-session safety and concurrent same-id runs
- ✅ Markdown scenario documents (header, exactly one python block, shifted diagnostics), `script=`/`ad_hoc=`
  input modes, and the `choice()` epilogue (evaluate-all verdicts, per-label table, no-match failure)
- ✅ expect_text regex in buffer mode
- ✅ `_update_buffer`/`drain_buffer` multi-pass drain with ratatui app (integration)
- ✅ screen_text_changed (threshold diff, edge cases)
- ✅ poll_until_stable / poll_until_changed (stable returns, timeout raises)
- ✅ auto session ID generation
- ✅ inter_key_delay parameter (send_keys char-by-char)
- ✅ get_char integration test
- ✅ `send_special_keys` declarative key names (arrows, f-keys, etc.)
- ✅ `screen_lines` is column-exact and survives a stale wide-char stub (the pyte `Screen.display`
  `IndexError` repro is a regression test)
- ✅ Liveness: a dead child raises on buffer reads and on writes, while a stream read still returns a
  finished command's output (unit + integration)
- ✅ `resize` moves the pty winsize *and* the emulated screen; the integration test proves the new rows
  exist, and the real-app smoke reached the resize-only responsive branch
- ✅ `screenshot` PNG (colour, cursor, wide glyphs) and the font coverage chain (every glyph a TUI
  draws renders as itself, not `.notdef`); `gif` stitching and its validation errors
- ✅ `shot`/`gif`/`resize`/`style` DSL steps, their argument validation and buffer-only warning
- ✅ Colour: `get_cell_style` reads ANSI/256/truecolour and every attribute; the PNG paints ANSI red,
  256-colour and background exactly, and truecolour within antialiasing; colour emoji render chromatic
  (integration + unit, incl. a skip guard when no symbol/emoji font is installed)
- ❌ Pixel-level fidelity against a real terminal (fonts differ; the cell grid, colours and attributes are
  what is asserted)
- ❌ Multi-session scenarios
- ❌ Dimensions parse error (added recently)
- ❌ Timing stress tests (rapid key sequences, tight timeouts)
- ❌ `screenshot`/`gif` under a non-default `MCP_TUI_TEST_FONT`

### Writing tests
- Add unit tests to `tests/unit/`, integration tests to `tests/integration/test_tools.py`
- Integration tests: create a `@pytest.mark.integration @pytest.mark.asyncio` class, use `mcp.call_tool()` to simulate agent calls
- Use `launch_tui` + `close_session` fixtures (see `launched_session` or `buffer_launched` patterns)
- `DummyProcess` fields: `before`, `after` (canned output), `sent` (appends on `send()`), `_closed` (boolean)

## Agent skill (`skills/tui-test/`)

The repo ships an agent skill that documents how to use these tools for TUI scenario walkthroughs:
pre-registered expectations, a machine-replayable scenario script (linted and executed by `run_scenario`), a transcript as the only
evidence, a visual polish rubric, and a findings review. Execution is the `run_scenario` tool. A scenario is a markdown document (header with `name`/`description`,
prose, exactly one python block) stored under `docs/guides/tui/` and indexed in
`docs/guides/tui/INDEX.md`; `ad_hoc=` accepts a bare step list for iteration. The tool lints
(`dry_run=True` for a lint-only pass), resolves the steps into tool calls, runs them, and returns the
transcript plus structured fields (`ok`, `header`, `diagnostics`, `pipeline`, `results`, `choice`,
`rescue_files`). A `choice({...})` epilogue — the last instruction, assertions only — evaluates every
alternative and reports every label that matched, failing the step when nothing matches.

`.omp/mcp.json` mounts this server into oh-my-pi sessions under the name `tui-test`, exposing the tools
as `mcp__tui_test_<tool>`. Keep the skill's references in sync with `_server.py` when tool signatures or
return strings change; `references/tools.md` is the contract agents read instead of the source.

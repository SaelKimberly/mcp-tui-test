# Repository Guidelines

## Project Overview

`mcp-tui-test` is a Python MCP (Model Context Protocol) server for testing Terminal User Interface applications programmatically. "Like Playwright, but for TUIs." It launches a TUI app in a subprocess, feeds input, captures screen output — all via 18 MCP tools callable by AI agents. Supports two modes: **stream** (pexpect, for CLI tools) and **buffer** (pyte terminal emulation, for full TUIs with position-aware assertions).

Version 0.4.0. MIT license. Two-module core (~810 combined lines).

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
| `capture()` | `process.before` + `process.after`, ANSI-stripped | `screen.display` via drain loop |
| `expect_text()` | `pexpect.expect()` (regex-native) | `re.search()` polling `screen.display` |
| Cursor | `(-1, -1)` always | pyte cursor position |
| Read tools | N/A | `get_line`, `get_screen_region`, `assert_at_position`, `get_cursor_position`, `get_char` |

**Key types:**

- `CursorPosition(row, col)` — frozen+slots dataclass
- `ScreenModel(lines, cursor, width, height, mode)` — frozen+slots dataclass; `.text` property joins lines
- `ScreenSession` — wraps pexpect + pyte; methods: `send()`, `capture()`, `_update_buffer()`, `close()`; fields: `launch_time`, `command`, `_last_preview`
- `sessions: dict[str, ScreenSession]` — module-level mutable state
- `require_session(id)` — lookup or `ToolError`
- `SPECIAL_KEYS: dict[str, str]` — maps key names (e.g. "up", "f1") to terminal escape sequences

**Helper module (`_screen.py`):**

- `ANSI_ESCAPE` regex + `strip_ansi(text)` — ANSI escape removal
:- `drain_buffer(process, screen, stream, max_passes=3, max_reads=100)` — multi-pass pyte drain with safety cap to prevent indefinite hang (extracted from `ScreenSession._update_buffer`)
- `screen_text_changed(a, b, threshold=0.05)` — line-aware diff for polling
- `poll_until_stable(session, timeout, interval, consecutive)` — wait for screen to stop changing
- `poll_until_changed(session, reference, timeout, interval)` — wait for screen to differ from reference

:**Buffer drain:** `_update_buffer()` delegates to `drain_buffer()` in `_screen.py` — 3 drain passes (timeout=0, 0.1s gap, max 100 reads per pass) via `process.read_nonblocking()`. Falls back to raw pexpect buffer (ANSI-stripped via `strip_ansi`) in `expect_text` if pyte doesn't track the pattern.

## Key Directories

```
./
├── src/mcp_tui_test/       # Package root
│   ├── _screen.py          # Screen polling/detection helpers (extracted from _server.py)
│   ├── _scenario.py        # Scenario script parsing/lint/pipeline (pure: no session, no subprocess)
│   ├── _server.py          # Dataclasses, ScreenSession, 18 MCP tools (incl. run_scenario)
│   ├── __init__.py          # Exports `mcp` FastMCP instance
│   └── __main__.py          # `python -m mcp_tui_test`
├── tests/
│   ├── conftest.py          # DummyProcess, session/buffer_session fixtures
│   ├── unit/                # 137 tests, no subprocesses
│   │   ├── test_screen.py
│   │   ├── test_screen_session.py
│   │   ├── test_capture.py
│   │   └── test_errors.py
│   └── integration/         # 54 tests, real subprocess via mcp.call_tool()
│       └── test_tools.py
├── examples/
│   ├── example_tui_app.py   # Simple print/input menu (not a real TUI)
│   └── test_xray_tui.py     # Probe script for ratatui app diagnosis
├── docs/                    # MkDocs website source
│   ├── index.md             # Doc page (lags code)
│   ├── examples.md          # Doc page (lags code)
│   └── stylesheets/
├── skills/tui-test/         # Agent skill: TUI scenario walkthrough workflow
│   ├── SKILL.md             # Workflow, doc structure, gotcha table
│   └── references/          # tools.md, scenario-template.md, polish-rubric.md, mounting.md
├── .omp/mcp.json            # oh-my-pi project mount of this repo's own MCP server
└── EXAMPLES.md              # 13 worked examples
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
uv run pytest -m "not integration" -v    # Unit tests only (fast, 137 tests)
uv run pytest -v                         # Full suite (unit + integration, ~37s)
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

### MCP tool signature patterns
- All tools accept `session_id: str = "default"`
- Return a human-readable string with `✓` prefix on success
- Buffer-only tools (`assert_at_position`, `get_line`, `get_screen_region`, `get_cursor_position`) don't check mode — they call `session.capture()` and work from ScreenModel; stream-mode sessions return meaningless data gracefully
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
| `src/mcp_tui_test/_server.py` | Dataclasses, ScreenSession, 18 tools incl. `run_scenario` |
| `src/mcp_tui_test/_scenario.py` | Scenario script (DSL) parser, lint diagnostics, pipeline resolution, report rendering |
| `src/mcp_tui_test/__init__.py` | Exports `mcp` |
| `src/mcp_tui_test/__main__.py` | Entry point: `mcp.run()` |
| `pyproject.toml` | Build config, deps, scripts, pytest settings |
| `uv.lock` | Lockfile (revision 3), all transitive deps pinned |
| `tests/conftest.py` | DummyProcess + fixtures |
| `tests/unit/test_screen.py` | Unit tests for _screen.py helpers |
| `tests/unit/test_screen_session.py` | Unit tests for ScreenSession |
| `tests/integration/test_tools.py` | Integration tests for all 18 tools |
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
- **Unit tests** (no marker): 5 files, 137 tests (`test_scenario.py` covers the scenario parser/linter with no subprocess). Use `DummyProcess` to mock pexpect. Cover ScreenSession init/send/capture/close, ScreenModel/CursorPosition immutability, require_session error handling, `_screen.py` helper functions (screen_text_changed, poll_until_stable, poll_until_changed, drain_buffer). Fast (~2s).
- **Integration tests** (`@pytest.mark.integration`): 1 file, 54 tests (48 run without the ratatui fixture; 6 error unless it exists). Launch real subprocess via `mcp.call_tool()`. Cover all 18 tools across launch/expect/send/assert/buffer-mode/Ctrl-key/special-keys/`run_scenario` scenarios, plus 6 ratatui tests that error unless the fixture binary exists at `/tmp/ratatui-test-app/target/release/ratatui-test-app`. Slower (~45s).

### What's tested vs gaps
- ✅ ScreenSession init, send, capture (stream+buffer), close
- ✅ ScreenModel/CursorPosition immutability, text property
- ✅ require_session lookup/error
- ✅ All 18 MCP tools at integration level (smoke)
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
- ❌ Multi-session scenarios
- ❌ `preserve_format` parameter paths (added recently)
- ❌ Dimensions parse error (added recently)
- ❌ Timing stress tests (rapid key sequences, tight timeouts)

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

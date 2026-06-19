# Repository Guidelines

## Project Overview

`mcp-tui-test` is a Python MCP (Model Context Protocol) server for testing Terminal User Interface applications programmatically. "Like Playwright, but for TUIs." It launches a TUI app in a subprocess, feeds input, captures screen output — all via 12 MCP tools callable by AI agents. Supports two modes: **stream** (pexpect, for CLI tools) and **buffer** (pyte terminal emulation, for full TUIs with position-aware assertions).

Version 0.3.0. MIT license. Single-module implementation (~542 lines).

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
4. `list_sessions()` dumps active sessions

**Two modes branch at every level:**

| Layer | Stream | Buffer |
|---|---|---|
| Backend | pexpect accumulators (`before`/`after`) | pexpect → pyte `Screen`/`Stream` |
| `capture()` | `process.before` + `process.after`, ANSI-stripped | `screen.display` via drain loop |
| `expect_text()` | `pexpect.expect()` (regex-native) | `re.search()` polling `screen.display` |
| Cursor | `(-1, -1)` always | pyte cursor position |
| Read tools | N/A | `get_line`, `get_screen_region`, `assert_at_position`, `get_cursor_position` |

**Key types:**

- `CursorPosition(row, col)` — frozen+slots dataclass
- `ScreenModel(lines, cursor, width, height, mode)` — frozen+slots dataclass; `.text` property joins lines
- `ScreenSession` — wraps pexpect + pyte; methods: `send()`, `capture()`, `_update_buffer()`, `close()`
- `sessions: Dict[str, ScreenSession]` — module-level mutable state
- `require_session(id)` — lookup or `ToolError`

**Buffer drain:** `_update_buffer()` does 3 drain passes (0.05s timeout, 0.1s gap) via `process.read_nonblocking()` — catches staggered/batched ratatui output that a single pass would miss. Falls back to raw pexpect buffer (ANSI-stripped) in `expect_text` if pyte doesn't track the pattern.

## Key Directories

```
./
├── src/mcp_tui_test/       # Package root
│   ├── _server.py          # Everything: dataclasses, ScreenSession, 12 MCP tools
│   ├── __init__.py          # Exports `mcp` FastMCP instance
│   └── __main__.py          # `python -m mcp_tui_test`
├── tests/
│   ├── conftest.py          # DummyProcess, session/buffer_session fixtures
│   ├── unit/                # 25 tests, no subprocesses
│   │   ├── test_screen_session.py
│   │   ├── test_capture.py
│   │   └── test_errors.py
│   └── integration/         # 24 tests, real subprocess via mcp.call_tool()
│       └── test_tools.py
├── examples/
│   ├── example_tui_app.py   # Simple print/input menu (not a real TUI)
│   └── test_xray_tui.py     # Probe script for ratatui app diagnosis
├── docs/                    # MkDocs website source
│   ├── index.md             # Doc page (lags code)
│   ├── examples.md          # Doc page (lags code)
│   └── stylesheets/
└── EXAMPLES.md              # 12 worked examples
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
uv run pytest -m "not integration" -v    # Unit tests only (fast, 25 tests)
uv run pytest -v                         # Full suite (unit + integration, ~37s)

# Run a single test file
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

No linting, formatting, or type-checking commands configured (no ruff/black/mypy in dev deps).

## Code Conventions & Common Patterns

### Module structure
- Single-file core: `_server.py` contains all dataclasses, the `ScreenSession` class, the `sessions` dict, and all 12 `@mcp.tool` functions
- Tool functions are module-level, decorated with `@mcp.tool`, take plain params (not dependency-injected)
- No classes or tool registrations outside `_server.py`

### Error handling
- **Expected failures**: raise `ToolError("message")` from `fastmcp.exceptions` — FastMCP surfaces these as clean MCP errors
- **Unexpected errors**: let raw exceptions propagate — FastMCP wraps them into ToolError with traceback
- Format: `raise ToolError(f"Assertion failed: '{text}' not found in output")`
- Session lookup failure → `ToolError("No active session found: '{session_id}'")`

### Session management
- Global `sessions: Dict[str, ScreenSession]` — single source of truth
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
| `src/mcp_tui_test/_server.py` | Everything: ~542 lines, 12 tools, ScreenSession, dataclasses |
| `src/mcp_tui_test/__init__.py` | Exports `mcp` |
| `src/mcp_tui_test/__main__.py` | Entry point: `mcp.run()` |
| `pyproject.toml` | Build config, deps, scripts, pytest settings |
| `uv.lock` | Lockfile (revision 3), all transitive deps pinned |
| `tests/conftest.py` | DummyProcess + fixtures |
| `tests/integration/test_tools.py` | Integration tests exercising all tools |
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
- **Formatters/linters**: None configured (no ruff, black, mypy in dev deps)
- **Documentation site**: MkDocs with cinder theme, mermaid2 plugin, localsearch plugin
- **Claude Desktop config**: `claude_desktop_config.example.json` — command is `mcp-tui-test` (console script)

## Testing & QA

### Framework
- **pytest** ≥9.1.0 with **pytest-asyncio** ≥1.4
- `asyncio_mode = "auto"` — async test functions work without decorators
- Custom marker: `integration` — deselect with `-m "not integration"`

### Test boundaries
- **Unit tests** (no marker): 3 files, 25 tests. Use `DummyProcess` to mock pexpect. Cover ScreenSession init/send/capture/close, ScreenModel/CursorPosition immutability, require_session error handling. Fast (~1s).
- **Integration tests** (`@pytest.mark.integration`): 1 file, 24 tests. Launch real subprocess via `mcp.call_tool()`. Cover all 12 tools across launch/expect/send/assert/buffer-mode/Ctrl-key/ratatui scenarios. Slower (~36s).

### What's tested vs gaps
- ✅ ScreenSession init, send, capture (stream+buffer), close
- ✅ ScreenModel/CursorPosition immutability, text property
- ✅ require_session lookup/error
- ✅ All 12 MCP tools at integration level (smoke)
- ✅ expect_text regex in buffer mode
- ✅ `_update_buffer` multi-pass drain with ratatui app (integration)
- ❌ Multi-session scenarios
- ❌ `preserve_format` parameter paths (added recently)
- ❌ Dimensions parse error (added recently)
- ❌ Timing stress tests (rapid key sequences, tight timeouts)

### Writing tests
- Add unit tests to `tests/unit/`, integration tests to `tests/integration/test_tools.py`
- Integration tests: create a `@pytest.mark.integration @pytest.mark.asyncio` class, use `mcp.call_tool()` to simulate agent calls
- Use `launch_tui` + `close_session` fixtures (see `launched_session` or `buffer_launched` patterns)
- `DummyProcess` fields: `before`, `after` (canned output), `sent` (appends on `send()`), `_closed` (boolean)

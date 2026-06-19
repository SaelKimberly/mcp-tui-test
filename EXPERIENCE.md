
#### TUI Testing (tui-test Tools)

The MCP tui-test server provides 12 tools for automated TUI interaction via a headless pyte terminal emulator. Use these for inspecting current appearance and behavior before making UX changes.

**Tool activation**: Run `search_tool_bm25` with query `"tui-test tools capabilities"` first — they are discoverable but not activated by default.

**Launch** — always use buffer mode for ratatui apps:
```
mcp__tui_test_launch_tui(
  command: "/home/user/oss/xray-tui/target/release/xray-tui",
  session_id: "my-session",
  timeout: 15,
  dimensions: "120x40",
  mode: "buffer"
)
```
- **buffer mode** — pyte terminal emulator; captures rendered screen state including box-drawing characters
- **stream mode** — line-buffered stdout; only captures text lines TUI writes, NOT the rendered screen
- Use `expect_text(pattern="Profiles", timeout=5)` after launch to wait for the TUI to finish its first render cycle
- Minimum dimensions 120×40; smaller crops content

**Reading the screen:**
- `capture_screen` — full screen dump; **may strip or hide box-drawing characters** (ratatui cell-diff rendering vs pyte tracking). Use `get_screen_region` or `get_line` instead for accurate reads.
- `get_line(row=N)` — single row of the buffer; preserves box-drawing (`│`, `─`, `┌`, etc.) and text content
- `get_screen_region(row_start, row_end, col_start, col_end)` — rectangular slice; most reliable for inspecting specific areas
- `assert_contains(text)` — substring check; raises `ToolError` on failure
- `assert_at_position(text, row, col)` — exact cell check (buffer mode only)

**Sending input:**
- `send_keys(keys, delay=0.3)` — send keystrokes with inter-key pause. Escape sequences: `\n` (Enter — **may be unreliable** in some ratatui modes; try `\r` as fallback), `\t` (Tab), `\x1b` (Escape). Minimum 0.1s delay; use ≥0.3s for chained multi-key sequences.
- `send_ctrl(key)` — single Ctrl+Letter combos (`'c'` → Ctrl+C, `'d'` → Ctrl+D, etc.)
- Always `expect_text` after sending keys to wait for the screen to update before reading.

**Session lifecycle:**
- Sessions persist across tool calls within the same session_id. Re-launching with the same name closes the old session automatically.
- `close_session(session_id)` — terminate the subprocess and release resources. Always call when done.
- `list_sessions` — show active sessions. Use to check for stale sessions before a fresh launch.
- Stale sessions left from interrupted work will block fresh launches with the same `session_id`.

**Known issues:**
- **Ratatui + pyte mismatch**: ratatui writes cell-by-cell diffs using alternate-screen + raw mode. Pyte's cell tracking may not reflect every render, especially for full-screen modes like the Settings panel. If `capture_screen` shows a blank area where you expect content, use `get_line`/`get_screen_region` instead — the data is in the buffer even if the full dump misses it.
- **Enter key ambiguity**: `\n` may map differently in different crossterm states. If Enter doesn't trigger the expected action, try `\r` (Ctrl+M) as an alternative.
- **Timing**: ratatui processes input asynchronously. Always add `delay ≥ 0.3` to `send_keys` and `expect_text` after key sequences before reading the result.
- **Multi-session isolation**: testing multiple TUI instances concurrently requires distinct `session_id` values. Cross-session input/output is fully isolated.

**Workflow pattern (read-inspect-read):**
```
1. launch_tui (buffer)
2. expect_text("Profiles")      # wait for ready
3. get_screen_region(…)         # capture baseline
4. send_keys("a", delay=0.3)    # action
5. expect_text("Select Protocol") # verify transition
6. get_line(N) / get_screen_region(…)  # capture result
7. send_keys("\x1b")            # go back
8. expect_text("Profiles")      # verify return
9. close_session
```

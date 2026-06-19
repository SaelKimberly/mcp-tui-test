#!/usr/bin/env python3
"""MCP Server for TUI Testing
Like Playwright, but for Terminal User Interfaces

Supports two modes:
- stream: Traditional pexpect stream-based testing (good for CLIs)
- buffer: Screen buffer testing with pyte (good for full TUIs)
"""

import codecs
import itertools
import re
import time
from dataclasses import dataclass

import pexpect
import pyte
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from mcp_tui_test._screen import drain_buffer, strip_ansi

# Initialize FastMCP server
mcp = FastMCP("tui-test")


@dataclass(frozen=True, slots=True)
class CursorPosition:
    row: int
    col: int


@dataclass(frozen=True, slots=True)
class ScreenModel:
    lines: tuple[str, ...]
    cursor: CursorPosition
    width: int
    height: int
    mode: str

    @property
    def text(self) -> str:
        """Join all lines into a single string."""
        return "\n".join(self.lines)


def require_session(session_id: str) -> ScreenSession:
    """Look up session by ID or raise ToolError if not found."""
    if session_id not in sessions:
        raise ToolError(f"No active session found: {session_id}")
    return sessions[session_id]


class ScreenSession:
    """Wrapper combining pexpect and pyte for hybrid stream/buffer testing.

    Attributes:
        process: pexpect.spawn instance
        screen: pyte.Screen instance (buffer mode)
        stream: pyte.Stream instance (buffer mode)
        mode: "stream" or "buffer"
        dimensions: (width, height) tuple

    """

    def __init__(
        self,
        process,  # duck-typed: .send(), .read_nonblocking(), .expect(), .before, .close()
        dimensions: tuple[int, int],
        mode: str = "stream",
    ):
        self.process = process
        self.mode = mode
        self.dimensions = dimensions
        self.launch_time: float = time.time()
        self.command: str = ""
        self._last_preview: str = ""
        width, height = dimensions

        # Initialize pyte screen buffer if in buffer mode
        if mode == "buffer":
            self.screen = pyte.Screen(width, height)
            self.stream = pyte.Stream(self.screen)
            # Start background task to feed output to pyte

    def _update_buffer(self):
        """Drain all available process output into pyte screen buffer.

        Delegates to drain_buffer in _screen.py.
        """
        if self.mode == "buffer":
            drain_buffer(self.process, self.screen, self.stream)

    def send(self, keys: str, delay: float = 0.3):
        """Send keys to the process and wait for them to be processed."""
        self.process.send(keys)
        time.sleep(delay)
        if self.mode == "buffer":
            self._update_buffer()

    def capture(self, preserve_format: bool = False) -> ScreenModel:
        """Return a snapshot of the current terminal state.

        Args:
            preserve_format: If True, keep trailing whitespace and ANSI codes
                             for an accurate picture of the terminal layout.

        """
        if self.mode == "buffer":
            self._update_buffer()
            lines = tuple(
                line if preserve_format else line.rstrip()
                for line in self.screen.display
            )
            # Store preview for list_sessions
            non_empty = [l for l in lines if l.strip()]
            self._last_preview = (non_empty[0][:80] if non_empty else "")[:80]
            return ScreenModel(
                lines=lines,
                cursor=CursorPosition(self.screen.cursor.y, self.screen.cursor.x),
                width=self.dimensions[0],
                height=self.dimensions[1],
                mode="buffer",
            )
        # Combine before and after to capture last expect() result
        parts = []
        if self.process.before:
            parts.append(self.process.before)
        after = getattr(self.process, "after", None)
        if isinstance(after, (str, bytes)):
            if isinstance(after, bytes):
                after = after.decode("utf-8", errors="replace")
            if after:
                parts.append(after)
        combined = "".join(parts) if parts else ""
        if not preserve_format:
            combined = strip_ansi(combined)
        lines = tuple(combined.split("\n")) if combined else ("",)
        # Store preview for list_sessions
        non_empty = [l for l in lines if l.strip()]
        self._last_preview = (non_empty[0][:80] if non_empty else "")[:80]
        return ScreenModel(
            lines=lines,
            cursor=CursorPosition(-1, -1),
            width=self.dimensions[0],
            height=self.dimensions[1],
            mode="stream",
        )

    def close(self):
        """Close the session."""
        self.process.close()


# Store active TUI sessions
# Auto-incrementing counter for auto-generated session IDs
_session_counter = itertools.count(1)
sessions: dict[str, ScreenSession] = {}


@mcp.tool
def launch_tui(
    command: str,
    session_id: str = "default",
    timeout: int = 30,
    dimensions: str | None = "80x24",
    mode: str = "buffer",
) -> str:
    """Launch a TUI app in a subprocess for automated testing.

    Call this first — creates a session that all other tools reference.
    **Buffer mode** (default): full terminal emulation via pyte, position-aware assertions.
    **Stream mode**: line-by-line pexpect accumulators for CLI tools.

    Args:
        command: Shell command to run (e.g. "python my_app.py")
        session_id: Label for this session (default: "default")
        timeout: Kill subprocess after N seconds (default: 30)
        dimensions: Terminal size as WxH, e.g. "80x24" (default: "80x24")
        mode: "buffer" for full TUIs, "stream" for line-by-line CLI (default: "buffer")

    Returns:
        Confirmation with session ID, command, dimensions, and mode

    """
    # Parse dimensions
    if dimensions:
        try:
            width, height = map(int, dimensions.split("x"))
        except ValueError, AttributeError:
            raise ToolError(
                f"Invalid dimensions format: '{dimensions}'. "
                "Expected format like '80x24' (WxH).",
            )
    else:
        width, height = 80, 24

    # Auto-generate session ID if empty
    if not session_id:
        session_id = f"session_{next(_session_counter)}"

    # Close existing session if it exists
    if session_id in sessions:
        sessions[session_id].close()
        del sessions[session_id]

    # Spawn the TUI process
    process = pexpect.spawn(
        command,
        timeout=timeout,
        dimensions=(height, width),
        encoding="utf-8",
    )

    # Launch the TUI application session
    session = ScreenSession(process=process, dimensions=(width, height), mode=mode)
    session.command = command

    sessions[session_id] = session

    # Give it a moment to initialize
    time.sleep(0.5)

    return f"✓ Launched TUI application (session: {session_id})\nCommand: {command}\nDimensions: {width}x{height}\nMode: {mode}"


@mcp.tool
def send_keys(
    keys: str,
    session_id: str = "default",
    delay: float = 0.3,
    inter_key_delay: float = 0.0,
) -> str:
    """Send keystrokes to the running TUI application.

    Use after expect_text to interact with the app.
    Supports escape sequences: \\r (Enter), \\t (Tab), \\x1b (Escape).
    Adds a configurable pause after sending so the app processes input.

    ⚠️ Raw-mode TUI apps (ratatui/crossterm) require \\r not \\n for Enter.
    Use send_keys("q\\r") to type "q" then press Enter.

    Set inter_key_delay > 0 to send characters one at a time with a
    pause between each — useful for apps that buffer input or animate
    per-character. Default 0.0 sends all keys at once (unchanged behavior).

    Args:
        keys: Keystrokes to send. Use \\n for Enter, \\x1b for Escape.
        session_id: Session identifier (default: "default")
        delay: Seconds to wait after sending for the app to process input (default: 0.3, minimum 0.1)
        inter_key_delay: Seconds between each character (default: 0.0, send all at once)

    Returns:
        Confirmation message

    """
    # Enforce minimum delay so the app has time to process input
    delay = max(delay, 0.1)
    session = require_session(session_id)
    # Decode Python escape sequences (\x1b, \n, \t, etc.)
    decoded_keys = codecs.decode(keys, "unicode_escape")
    if inter_key_delay > 0:
        for char in decoded_keys:
            session.process.send(char)
            time.sleep(inter_key_delay)
    else:
        session.process.send(decoded_keys)
    time.sleep(delay)
    if session.mode == "buffer":
        session._update_buffer()

    return f"✓ Sent keys to session {session_id}"


@mcp.tool
def capture_screen(
    session_id: str = "default",
    include_ansi: bool = False,
    preserve_format: bool = False,
) -> str:
    """Snapshot the current terminal screen as plain text.

    Buffer mode returns the pyte terminal emulator display; stream mode
    returns accumulated pexpect output. Strips ANSI codes and trailing
    whitespace by default.

    preserve_format=True bypasses all stripping for exact terminal layout.
    include_ansi keeps ANSI codes (stream mode only; ignored when preserve_format=True).

    ⚠️ pyte may not perfectly track ratatui box-drawing characters (│, ─, ┌).
    For character-level inspection, use get_line() or get_screen_region()
    — they return less context so data is easier to read character-by-character.

    Args:
        session_id: Session identifier (default: "default")
        include_ansi: Keep ANSI codes in stream mode output (default: False).
                      Ignored when preserve_format=True.
        preserve_format: Bypass trimming and ANSI stripping for exact layout
                         (default: False)

    Returns:
        Screen content block with session info and mode label

    """
    session = require_session(session_id)
    model = session.capture(preserve_format=preserve_format)
    output = model.text
    if include_ansi and session.mode == "stream" and not preserve_format:
        output = session.process.before or ""
    mode_label = session.mode
    return f"Screen output (session: {session_id}, mode: {mode_label}):\n{'=' * 60}\n{output}\n{'=' * 60}"


@mcp.tool
def expect_text(pattern: str, session_id: str = "default", timeout: int = 10) -> str:
    """Wait for output to contain a pattern (blocking, with timeout).

    Use after launch_tui / send_keys to synchronize with the app before
    reading state or sending more input.
    Buffer mode polls the pyte display; stream mode uses pexpect.expect().
    Raises ToolError on timeout or EOF.

    Args:
        pattern: Literal text or regex pattern to wait for
        session_id: Session identifier (default: "default")
        timeout: Max seconds to wait (default: 10)

    Returns:
        Confirmation when found

    Raises:
        ToolError: Timeout or EOF before match

    """
    session = require_session(session_id)

    if session.mode == "buffer":
        # Buffer mode: poll pyte screen for pattern
        compiled = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            session._update_buffer()
            display = session.screen.display
            if any(compiled.search(line) for line in display):
                return f"✓ Found expected text: '{pattern}'"
            time.sleep(0.1)
        # Fallback: check raw pexpect buffer (ANSI-stripped) for patterns
        # that pyte may not have tracked correctly
        raw = (session.process.before or "") + (
            getattr(session.process, "after", None) or ""
        )
        raw_clean = strip_ansi(raw)
        if compiled.search(raw_clean):
            session._update_buffer()
            return f"✓ Found expected text: '{pattern}' (from raw output)"
        model = session.capture()
        dump = "\n".join(model.lines[:40])
        raise ToolError(
            f"Pattern '{pattern}' not found after {timeout}s.\n"
            f"Current screen ({len(model.lines)} lines):\n{dump}",
        )

    # Stream mode: use pexpect.expect()
    session.process.timeout = timeout
    index = session.process.expect([pattern, pexpect.TIMEOUT, pexpect.EOF])

    if index == 0:
        return f"✓ Found expected text: '{pattern}'"
    if index == 1:
        model = session.capture()
        dump = "\n".join(model.lines[:40])
        raise ToolError(
            f"Pattern '{pattern}' not found after {timeout}s.\n"
            f"Current screen ({len(model.lines)} lines):\n{dump}",
        )
    model = session.capture()
    dump = "\n".join(model.lines[:40])
    raise ToolError(
        f"EOF reached before finding: '{pattern}'\n"
        f"Last screen ({len(model.lines)} lines):\n{dump}",
    )


@mcp.tool
def assert_contains(text: str, session_id: str = "default") -> str:
    """Assert the current screen content contains the given substring.

    Like capture_screen + substring search as a single call.
    Raises ToolError on failure (clean error surfaced by MCP host).

    Args:
        text: Substring to search for in the current screen
        session_id: Session identifier (default: "default")

    Returns:
        Confirmation when found

    Raises:
        ToolError: Text not found in output

    """
    session = require_session(session_id)
    model = session.capture()
    if text in model.text:
        return f"✓ Assertion passed: Found '{text}' in output"
    raise ToolError(f"Assertion failed: '{text}' not found in output")


@mcp.tool
def assert_at_position(
    text: str,
    row: int,
    col: int,
    session_id: str = "default",
) -> str:
    """Assert text appears at a specific (row, col) position (buffer mode only).

    Slices the screen line at `row` from `col` for the length of `text`,
    comparing after stripping whitespace. Verifies exact layout, not just content.

    Args:
        text: Expected text at the position
        row: 0-indexed row number
        col: 0-indexed column number
        session_id: Session identifier (default: "default")

    Returns:
        Confirmation when found

    Raises:
        ToolError: Row out of bounds, or text mismatch at position

    """
    session = require_session(session_id)
    model = session.capture()
    if row >= len(model.lines):
        raise ToolError(f"Assertion failed: row {row} out of bounds")
    line = model.lines[row]
    actual = line[col : col + len(text)]
    if actual.strip() == text.strip():
        return f"✓ Assertion passed: Found '{text}' at position ({row}, {col})"
    raise ToolError(
        f"Assertion failed: Expected '{text}' at ({row},{col}) but found '{actual.strip()}'",
    )


@mcp.tool
def get_cursor_position(session_id: str = "default") -> str:
    """Read the cursor's current (row, col) in buffer mode.

    Uses pyte's cursor tracker. 0-indexed. Stream mode returns (-1, -1).

    Args:
        session_id: Session identifier (default: "default")

    Returns:
        Cursor position as (row, col)

    """
    session = require_session(session_id)
    model = session.capture()
    return f"Cursor position (session: {session_id}): row {model.cursor.row}, column {model.cursor.col}"


@mcp.tool
def get_screen_region(
    row_start: int,
    row_end: int,
    col_start: int = 0,
    col_end: int | None = None,
    session_id: str = "default",
) -> str:
    """Extract a rectangular region of the screen buffer (buffer mode only).

    Focus on a specific area instead of scanning the full screen.
    Ranges are Python-slice style: start inclusive, end exclusive.

    Args:
        row_start: First row to include (0-indexed, inclusive)
        row_end: Row to stop before (0-indexed, exclusive)
        col_start: First column to include (default: 0)
        col_end: Column to stop before (default: end of line)
        session_id: Session identifier (default: "default")

    Returns:
        Region text block with session info

    """
    session = require_session(session_id)
    model = session.capture()
    if col_end is None:
        col_end = model.width
    region_lines = [line[col_start:col_end] for line in model.lines[row_start:row_end]]
    region_text = "\n".join(region_lines)
    return f"Screen region (session: {session_id}, rows {row_start}-{row_end - 1}, cols {col_start}-{col_end - 1}):\n{'=' * 60}\n{region_text}\n{'=' * 60}"


@mcp.tool
def get_line(row: int, session_id: str = "default") -> str:
    """Get a single line from the screen buffer by row (buffer mode only).

    Args:
        row: 0-indexed row number
        session_id: Session identifier (default: "default")

    Returns:
        Line content

    Raises:
        ToolError: Row out of bounds

    """
    session = require_session(session_id)
    model = session.capture()
    if row >= len(model.lines):
        raise ToolError(f"Row {row} out of bounds")
    return f"Line {row} (session: {session_id}): {model.lines[row]}"


@mcp.tool
def wait_for_stable(
    interval: float = 0.1,
    consecutive: int = 3,
    timeout: int = 10,
    session_id: str = "default",
) -> str:
    """Wait for the TUI screen to stop changing (content stabilizes).

    Polls the screen every `interval` seconds until `consecutive`
    consecutive captures are identical. Essential after sending keys
    that trigger a re-render — replaces guesswork sleep() calls.

    Args:
        interval: Seconds between polls (default: 0.1)
        consecutive: Number of identical samples to confirm stability (default: 3)
        timeout: Max seconds to wait (default: 10)
        session_id: Session identifier (default: "default")

    Returns:
        The final stable screen content

    Raises:
        ToolError: Screen did not stabilize within timeout

    """
    session = require_session(session_id)
    try:
        from mcp_tui_test._screen import poll_until_stable

        text = poll_until_stable(
            session,
            timeout=timeout,
            interval=interval,
            consecutive=consecutive,
        )
    except TimeoutError as e:
        raise ToolError(str(e))
    return f"Screen stabilized (session: {session_id}):\n{'=' * 60}\n{text}\n{'=' * 60}"


@mcp.tool
def wait_for_change(
    reference: str,
    interval: float = 0.1,
    timeout: int = 10,
    session_id: str = "default",
) -> str:
    """Wait for the TUI screen content to change from a reference.

    Polls until screen differs from `reference`. Returns the new
    screen content. Ideal for detecting state transitions like
    loading spinners completing or menus opening.

    Args:
        reference: Previous screen text to compare against
        interval: Seconds between polls (default: 0.1)
        timeout: Max seconds to wait (default: 10)
        session_id: Session identifier (default: "default")

    Returns:
        The new screen content after change detected

    Raises:
        ToolError: Screen did not change within timeout

    """
    session = require_session(session_id)
    try:
        from mcp_tui_test._screen import poll_until_changed

        text = poll_until_changed(
            session,
            reference,
            timeout=timeout,
            interval=interval,
        )
    except TimeoutError as e:
        raise ToolError(str(e))
    return f"Screen changed (session: {session_id}):\n{'=' * 60}\n{text}\n{'=' * 60}"


@mcp.tool
def get_session_info(
    session_id: str = "default",
) -> str:
    """Get detailed metadata about a session.

    Returns command, mode, dimensions, PID, uptime, and a preview
    of the last captured screen content.

    Args:
        session_id: Session identifier (default: "default")

    Returns:
        Formatted session metadata

    """
    session = require_session(session_id)
    uptime = time.time() - session.launch_time
    pid = session.process.pid if hasattr(session.process, "pid") else "N/A"
    w, h = session.dimensions
    preview = session._last_preview or "(no capture yet)"
    return (
        f"Session: {session_id}\n"
        f"  Command: {session.command}\n"
        f"  Mode: {session.mode}\n"
        f"  Dimensions: {w}x{h}\n"
        f"  PID: {pid}\n"
        f"  Uptime: {uptime:.0f}s\n"
        f"  Preview: {preview}"
    )


@mcp.tool
def get_char(
    row: int,
    col: int,
    session_id: str = "default",
) -> str:
    """Get a single character at (row, col) on the screen (buffer mode only).

    Useful for character-level inspection of ratatui box-drawing or
    text alignment. Returns a space if the position is out of bounds.

    Args:
        row: 0-indexed row number
        col: 0-indexed column number
        session_id: Session identifier (default: "default")

    Returns:
        The character at that position (or a space)

    """
    session = require_session(session_id)
    model = session.capture()
    if row >= len(model.lines) or col >= model.width:
        return " "
    line = model.lines[row]
    if col >= len(line):
        return " "
    ch = line[col]
    return f"Character at ({row}, {col}): '{ch}'"


@mcp.tool
def close_session(session_id: str = "default") -> str:
    """Terminate the TUI subprocess and remove the session.

    Always call when done to clean up. Subsequent tools referencing
    this session_id will raise ToolError.

    Args:
        session_id: Session to close (default: "default")

    Returns:
        Confirmation

    """
    session = require_session(session_id)
    session.close()
    del sessions[session_id]

    return f"✓ Closed session: {session_id}"


@mcp.tool
def list_sessions() -> str:
    """List all active sessions with their IDs, modes, and metadata.

    Shows "No active sessions" when none exist.

    Returns:
        Session list, or "No active sessions" if none

    """
    if not sessions:
        return "No active sessions"

    lines_out = []
    for sid, session in sessions.items():
        uptime = time.time() - session.launch_time
        pid = session.process.pid if hasattr(session.process, "pid") else "N/A"
        w, h = session.dimensions
        preview = session._last_preview or "(no capture)"
        lines_out.append(
            f"  {sid} — {session.mode} (PID {pid}, {w}x{h}, "
            f'up {uptime:.0f}s) — "{preview}"',
        )
    return "Active sessions:\n" + "\n".join(lines_out)


@mcp.tool
def send_ctrl(key: str, session_id: str = "default") -> str:
    """Send a Ctrl+Key signal to the TUI application.

    Translates letters to control characters:
    send_ctrl("c") → Ctrl+C (SIGINT), send_ctrl("d") → Ctrl+D (EOF).

    Args:
        key: Letter to combine with Ctrl (e.g. 'c' for Ctrl+C)
        session_id: Session identifier (default: "default")

    Returns:
        Confirmation

    """
    session = require_session(session_id)

    # Convert key to control character
    ctrl_char = chr(ord(key.lower()) - ord("a") + 1)
    session.send(ctrl_char)

    return f"✓ Sent Ctrl+{key.upper()} to session {session_id}"


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""
MCP Server for TUI Testing
Like Playwright, but for Terminal User Interfaces

Supports two modes:
- stream: Traditional pexpect stream-based testing (good for CLIs)
- buffer: Screen buffer testing with pyte (good for full TUIs)
"""

import codecs
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pexpect
import pyte
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

# Initialize FastMCP server
mcp = FastMCP("tui-test")

# Compile ANSI escape regex once for performance
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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


def require_session(session_id: str) -> "ScreenSession":
    """Look up session by ID or raise ToolError if not found."""
    if session_id not in sessions:
        raise ToolError(f"No active session found: {session_id}")
    return sessions[session_id]


class ScreenSession:
    """
    Wrapper combining pexpect and pyte for hybrid stream/buffer testing.

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
        dimensions: Tuple[int, int],
        mode: str = "stream",
    ):
        self.process = process
        self.mode = mode
        self.dimensions = dimensions
        width, height = dimensions

        # Initialize pyte screen buffer if in buffer mode
        if mode == "buffer":
            self.screen = pyte.Screen(width, height)
            self.stream = pyte.Stream(self.screen)
            # Start background task to feed output to pyte

    def _update_buffer(self):
        """Drain all available process output into pyte screen buffer.

        Uses multiple short-drain passes with sleeps between to catch
        staggered or batched output that a single pass might miss.
        """
        if self.mode != "buffer":
            return
        # Multi-pass: staggered output may arrive between passes
        for _ in range(3):
            try:
                while True:
                    output = self.process.read_nonblocking(size=8192, timeout=0.05)
                    if not output:
                        break
                    self.stream.feed(output)
            except pexpect.TIMEOUT:
                pass  # No more output ready on this pass
            except pexpect.EOF:
                return  # Process ended — no point retrying
            time.sleep(0.1)

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
            combined = ANSI_ESCAPE.sub("", combined)
        lines = tuple(combined.split("\n")) if combined else ("",)
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
sessions: Dict[str, ScreenSession] = {}


@mcp.tool
def launch_tui(
    command: str,
    session_id: str = "default",
    timeout: int = 30,
    dimensions: Optional[str] = "80x24",
    mode: str = "buffer",
) -> str:
    """Launch a TUI application in a subprocess for automated testing.

    Use this first — creates a ScreenSession that other tools target.
    Supports buffer mode (full TUIs with terminal emulation via pyte)
    and stream mode (CLI tools, line-by-line output).

    Args:
        command: Shell command to run (e.g. "python my_app.py")
        session_id: Label for this session, referenced by other tools (default: "default")
        timeout: Seconds before the subprocess is killed (default: 30)
        dimensions: Terminal size as WxH, e.g. "80x24" (default: "80x24")
        mode: "buffer" (full terminal emulation, position-aware) or "stream" (line-by-line output). Default: "buffer" for TUI apps.

    Returns:
        Confirmation with session ID, command, dimensions, and mode
    """
    # Parse dimensions
    if dimensions:
        try:
            width, height = map(int, dimensions.split("x"))
        except (ValueError, AttributeError):
            raise ToolError(
                f"Invalid dimensions format: '{dimensions}'. "
                "Expected format like '80x24' (WxH)."
            )
    else:
        width, height = 80, 24

    # Close existing session if it exists
    if session_id in sessions:
        sessions[session_id].close()
        del sessions[session_id]

    # Spawn the TUI process
    process = pexpect.spawn(
        command, timeout=timeout, dimensions=(height, width), encoding="utf-8"
    )

    # Launch the TUI application session
    session = ScreenSession(process=process, dimensions=(width, height), mode=mode)

    sessions[session_id] = session

    # Give it a moment to initialize
    time.sleep(0.5)

    return f"✓ Launched TUI application (session: {session_id})\nCommand: {command}\nDimensions: {width}x{height}\nMode: {mode}"


@mcp.tool
def send_keys(keys: str, session_id: str = "default", delay: float = 0.3) -> str:
    """Send keystrokes to the running TUI application.

    Use after expect_text to interact with the app.
    Supports escape sequences: \\n (Enter), \\t (Tab), \\x1b (Escape).
    Adds a short pause after sending to let the app process input.

    Note: crossterm-based apps (ratatui, etc.) expect \\r (CR, 0x0D) for Enter
    in raw mode. \\n (LF, 0x0A) does NOT trigger Enter for such apps.
    Use send_keys("a\\r") to send "a" followed by Enter.

    Args:
        keys: Keystrokes to send. Use \\n for Enter, \\x1b for Escape.
        session_id: Session identifier (default: "default")
        delay: Seconds to wait after sending for the app to process input (default: 0.3, minimum 0.1)

    Returns:
        Confirmation message
    """
    # Enforce minimum delay so the app has time to process input
    delay = max(delay, 0.1)
    session = require_session(session_id)
    # Decode Python escape sequences (\x1b, \n, \t, etc.)
    decoded_keys = codecs.decode(keys, "unicode_escape")
    session.send(decoded_keys, delay=delay)

    return f"✓ Sent keys to session {session_id}"


@mcp.tool
def capture_screen(
    session_id: str = "default",
    include_ansi: bool = False,
    preserve_format: bool = False,
) -> str:
    """Snapshot the current terminal screen as plain text.

    In stream mode, returns accumulated process output (before/after buffer).
    In buffer mode, reads the pyte terminal emulator display.
    By default strips trailing whitespace and ANSI codes for clean output.

    Use preserve_format=True to bypass all stripping and see the exact
    terminal output including trailing spaces and ANSI escape codes.
    This is useful for UI/UX review of visual layout and styling.

    ⚠️ Buffer mode caveat: pyte's cell tracking may not perfectly reflect
    box-drawing characters (│, ─, ┌, etc.) from ratatui's cell-diff rendering.
    For reliable reads of specific areas, use get_line() or get_screen_region()
    — they read the same underlying buffer but return less context so the data
    is easier to inspect character-by-character.

    Args:
        session_id: Session identifier (default: "default")
        include_ansi: Keep ANSI codes in stream mode output (default: False).
                      Ignored when preserve_format=True.
        preserve_format: Bypass whitespace trimming and ANSI stripping for an
                         accurate picture of the actual terminal layout
                         (default: False)

    Returns:
        Screen content block with session info and mode label
    """
    session = require_session(session_id)
    model = session.capture(preserve_format=preserve_format)
    output = model.text
    if include_ansi and session.mode == "stream" and not preserve_format:
        output = session.process.before if session.process.before else ""
    mode_label = session.mode
    return f"Screen output (session: {session_id}, mode: {mode_label}):\n{'=' * 60}\n{output}\n{'=' * 60}"


@mcp.tool
def expect_text(pattern: str, session_id: str = "default", timeout: int = 10) -> str:
    """Wait for a pattern to appear in terminal output; raise ToolError on timeout.

    Use after launch_tui or send_keys to synchronize with the app.
    Stream mode uses pexpect.expect() on the subprocess output stream.
    Buffer mode polls the pyte screen display in a loop.

    Args:
        pattern: Literal text or regex pattern to wait for
        session_id: Session identifier (default: "default")
        timeout: Max seconds to wait (default: 10)

    Returns:
        Confirmation when found

    Raises:
        ToolError: Timeout waiting for pattern, or EOF reached before match
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
        raw = (session.process.before or "") + (getattr(session.process, "after", None) or "")
        raw_clean = ANSI_ESCAPE.sub("", raw)
        if compiled.search(raw_clean):
            session._update_buffer()
            return f"✓ Found expected text: '{pattern}' (from raw output)"
        raise ToolError(f"Timeout waiting for: '{pattern}'")

    # Stream mode: use pexpect.expect()
    session.process.timeout = timeout
    index = session.process.expect([pattern, pexpect.TIMEOUT, pexpect.EOF])

    if index == 0:
        return f"✓ Found expected text: '{pattern}'"
    elif index == 1:
        raise ToolError(f"Timeout waiting for: '{pattern}'")
    else:
        raise ToolError(f"EOF reached before finding: '{pattern}'")


@mcp.tool
def assert_contains(
    text: str, session_id: str = "default"
) -> str:
    """Verify that the current screen content contains the given text.

    Like capture_screen + substring check packaged as a single assertion.
    Raises ToolError on failure so the MCP host sees it as an error.

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
    text: str, row: int, col: int, session_id: str = "default"
) -> str:
    """Assert text appears at a specific (row, col) position (buffer mode only).

    Checks that the screen line at `row`, sliced at `col` for the length of
    `text`, matches after stripping whitespace. Useful for verifying exact
    layout rather than just content presence.

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
        f"Assertion failed: Expected '{text}' at ({row},{col}) but found '{actual.strip()}'"
    )


@mcp.tool
def get_cursor_position(session_id: str = "default") -> str:
    """Read the cursor's current (row, col) in buffer mode.

    Uses pyte's cursor tracker. Row/col are 0-indexed.
    Only meaningful in buffer mode — stream mode returns (-1, -1).

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
    col_end: Optional[int] = None,
    session_id: str = "default",
) -> str:
    """Extract a rectangular region of the screen buffer (buffer mode only).

    Useful for focusing on a specific area instead of scanning the full screen.
    Row and column ranges are Python-slice style: start inclusive, end exclusive.

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
def close_session(session_id: str = "default") -> str:
    """Terminate the TUI subprocess and remove the session.

    Always call this when done to clean up resources.
    Subsequent tool calls referencing this session_id will raise ToolError.

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
    """List all active sessions with their IDs and modes.

    Useful for discovery when multiple sessions exist, or to check
    state before operating.

    Returns:
        Session list, or "No active sessions" if none
    """
    if not sessions:
        return "No active sessions"

    session_list = "\n".join(
        [f"- {sid} (mode: {session.mode})" for sid, session in sessions.items()]
    )
    return f"Active sessions:\n{session_list}"


@mcp.tool
def send_ctrl(key: str, session_id: str = "default") -> str:
    """Send a Ctrl+Key signal to the TUI application.

    Translates letters to control-character equivalents
    (e.g. 'c' → Ctrl+C / SIGINT, 'd' → Ctrl+D / EOF).

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

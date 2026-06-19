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
        """Update pyte screen buffer with latest process output."""
        if self.mode != "buffer":
            return

        try:
            # Read available output without blocking
            output = self.process.read_nonblocking(size=8192, timeout=0.1)
            if output:
                self.stream.feed(output)
        except pexpect.TIMEOUT:
            pass  # No new output, that's fine
        except pexpect.EOF:
            pass  # Process ended

    def send(self, keys: str):
        """Send keys to the process."""
        self.process.send(keys)
        time.sleep(0.1)
        if self.mode == "buffer":
            self._update_buffer()

    def capture(self) -> ScreenModel:
        """Return a snapshot of the current terminal state."""
        if self.mode == "buffer":
            self._update_buffer()
            return ScreenModel(
                lines=tuple(line.rstrip() for line in self.screen.display),
                cursor=CursorPosition(self.screen.cursor.y, self.screen.cursor.x),
                width=self.dimensions[0], height=self.dimensions[1], mode="buffer",
            )
        # Combine before and after to capture last expect() result
        parts = []
        if self.process.before:
            parts.append(self.process.before)
        after = getattr(self.process, 'after', None)
        if isinstance(after, (str, bytes)):
            if isinstance(after, bytes):
                after = after.decode("utf-8", errors="replace")
            if after:
                parts.append(after)
        combined = "".join(parts) if parts else ""
        combined = ANSI_ESCAPE.sub("", combined)
        lines = tuple(combined.split("\n")) if combined else ("",)
        return ScreenModel(lines=lines, cursor=CursorPosition(-1, -1),
                           width=self.dimensions[0], height=self.dimensions[1], mode="stream")


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
    mode: str = "stream",
) -> str:
    """
    Launch a TUI application for testing.

    Args:
        command: The command to launch the TUI application
        session_id: Unique identifier for this session (default: "default")
        timeout: Command timeout in seconds (default: 30)
        dimensions: Terminal dimensions as WIDTHxHEIGHT (default: "80x24")
        mode: Testing mode - "stream" for CLI tools, "buffer" for full TUIs (default: "stream")

    Returns:
        Status message with session ID
    """
    # Parse dimensions
    if dimensions:
        width, height = map(int, dimensions.split("x"))
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
def send_keys(keys: str, session_id: str = "default", delay: float = 0.1) -> str:
    """
    Send keyboard input to a TUI application.

    Args:
        keys: Keys to send. Special keys: \\n (Enter), \\t (Tab), \\x1b (Escape)
        session_id: Session identifier (default: "default")
        delay: Delay in seconds after sending keys (default: 0.1)

    Returns:
        Status message
    """
    session = require_session(session_id)
    # Decode Python escape sequences (\x1b, \n, \t, etc.)
    decoded_keys = codecs.decode(keys, "unicode_escape")
    session.send(decoded_keys)

    # Additional delay if requested
    if delay > 0.1:
        time.sleep(delay - 0.1)

    return f"✓ Sent keys to session {session_id}"


@mcp.tool
def capture_screen(
    session_id: str = "default",
    include_ansi: bool = False,
    use_buffer: Optional[bool] = None,
) -> str:
    """
    Capture the current screen output of a TUI application.

    Args:
        session_id: Session identifier (default: "default")
        include_ansi: Whether to include ANSI escape codes in stream mode (default: False)
        use_buffer: Force buffer mode if True, stream mode if False. Auto-detect if None (default: None)

    Returns:
        Current screen content
    """
    session = require_session(session_id)
    model = session.capture()
    output = model.text
    if include_ansi and session.mode == "stream":
        output = session.process.before if session.process.before else ""
    mode_label = session.mode
    return f"Screen output (session: {session_id}, mode: {mode_label}):\n{'=' * 60}\n{output}\n{'=' * 60}"


@mcp.tool
def expect_text(pattern: str, session_id: str = "default", timeout: int = 10) -> str:
    """
    Wait for specific text to appear in the TUI output.

    Args:
        pattern: Text or regex pattern to wait for
        session_id: Session identifier (default: "default")
        timeout: Maximum time to wait in seconds (default: 10)

    Returns:
        Status message indicating if text was found
    """
    session = require_session(session_id)

    if session.mode == "buffer":
        # Buffer mode: poll pyte screen for pattern
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            session._update_buffer()
            display = session.screen.display
            if any(pattern in line for line in display):
                return f"✓ Found expected text: '{pattern}'"
            time.sleep(0.1)
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
    text: str, session_id: str = "default", use_buffer: Optional[bool] = None
) -> str:
    """
    Assert that the current screen contains specific text.

    Args:
        text: Text to search for in the current screen
        session_id: Session identifier (default: "default")
        use_buffer: Check buffer if True, stream if False. Auto-detect if None (default: None)

    Returns:
        Success or failure message
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
    """
    Assert that specific text appears at a screen position (buffer mode only).

    Args:
        text: Text to verify at the position
        row: Row number (0-indexed)
        col: Column number (0-indexed)
        session_id: Session identifier (default: "default")

    Returns:
        Success or failure message
    """
    session = require_session(session_id)
    model = session.capture()
    if row >= len(model.lines):
        raise ToolError(f"Assertion failed: row {row} out of bounds")
    line = model.lines[row]
    actual = line[col:col + len(text)]
    if actual.strip() == text.strip():
        return f"✓ Assertion passed: Found '{text}' at position ({row}, {col})"
    raise ToolError(f"Assertion failed: Expected '{text}' at ({row},{col}) but found '{actual.strip()}'")


@mcp.tool
def get_cursor_position(session_id: str = "default") -> str:
    """
    Get the current cursor position (buffer mode only).

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
    """
    Extract a rectangular region of the screen (buffer mode only).

    Args:
        row_start: Starting row (0-indexed, inclusive)
        row_end: Ending row (0-indexed, exclusive)
        col_start: Starting column (0-indexed, inclusive, default: 0)
        col_end: Ending column (0-indexed, exclusive, default: end of line)
        session_id: Session identifier (default: "default")

    Returns:
        Text content of the specified region
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
    """
    Get a specific line from the screen buffer (buffer mode only).

    Args:
        row: Row number (0-indexed)
        session_id: Session identifier (default: "default")

    Returns:
        Text content of the specified line
    """
    session = require_session(session_id)
    model = session.capture()
    if row >= len(model.lines):
        raise ToolError(f"Row {row} out of bounds")
    return f"Line {row} (session: {session_id}): {model.lines[row]}"


@mcp.tool
def close_session(session_id: str = "default") -> str:
    """
    Close a TUI testing session.

    Args:
        session_id: Session identifier (default: "default")

    Returns:
        Status message
    """
    session = require_session(session_id)
    session.close()
    del sessions[session_id]

    return f"✓ Closed session: {session_id}"


@mcp.tool
def list_sessions() -> str:
    """
    List all active TUI testing sessions.

    Returns:
        List of active session IDs with their modes
    """
    if not sessions:
        return "No active sessions"

    session_list = "\n".join(
        [f"- {sid} (mode: {session.mode})" for sid, session in sessions.items()]
    )
    return f"Active sessions:\n{session_list}"


@mcp.tool
def send_ctrl(key: str, session_id: str = "default") -> str:
    """
    Send a Ctrl+Key combination to the TUI application.

    Args:
        key: The key to combine with Ctrl (e.g., 'c', 'd', 'z')
        session_id: Session identifier (default: "default")

    Returns:
        Status message
    """
    session = require_session(session_id)

    # Convert key to control character
    ctrl_char = chr(ord(key.lower()) - ord("a") + 1)
    session.send(ctrl_char)

    return f"✓ Sent Ctrl+{key.upper()} to session {session_id}"


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""MCP Server for TUI Testing
Like Playwright, but for Terminal User Interfaces

Supports two modes:
- stream: Traditional pexpect stream-based testing (good for CLIs)
- buffer: Screen buffer testing with pyte (good for full TUIs)
"""

import codecs
import itertools
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import pexpect
import pyte
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from mcp_tui_test._render import (
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    describe_screen_attributes,
    frames_to_gif,
    render_screen_png,
)
from mcp_tui_test._scenario import (
    Diagnostic,
    Plan,
    new_save_files,
    one_line,
    parse_document,
    parse_script,
    render_diagnostics,
    render_execution,
    render_pipeline,
    rescue_dirs,
    save_files,
)
from mcp_tui_test._screen import drain_buffer, screen_lines, strip_ansi

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
        session_id: str = "",
    ):
        self.process = process
        self.mode = mode
        self.dimensions = dimensions
        self.session_id = session_id
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

    def assert_alive(self) -> None:
        """Raise ToolError when the child process has exited.

        Only meaningful where a stale state would be *believed*: a full-screen
        app that died leaves its last frame in the pyte grid, and answering `✓`
        from it lets a walkthrough pass against a dead app. A **stream**-mode
        session is different — its accumulated pexpect output is a record, and
        reading it after a finite command (`git status`, `echo`) exits is the
        reason stream mode exists — so reads there are not guarded; writes are.

        The pexpect-style duck type is checked, so a test double without
        `isalive` is treated as alive.
        """
        isalive = getattr(self.process, "isalive", None)
        if not callable(isalive) or isalive():
            return
        signal = getattr(self.process, "signalstatus", None)
        status = getattr(self.process, "exitstatus", None)
        if signal:
            why = f"was killed by signal {signal}"
        elif status is not None:
            why = f"exited with status {status}"
        else:
            why = "has exited"
        raise ToolError(
            f"Session '{self.session_id or '?'}' {why}; the screen is frozen and keystrokes are not "
            "delivered. Capture the frame before the keystroke that quits, re-launch, or read the "
            "app's own log to find out why it died.",
        )

    def resize(self, width: int, height: int, delay: float = 0.3) -> None:
        """Resize the pseudo-terminal and the emulated screen.

        The kernel sends SIGWINCH to the child when the pty size changes, which
        is how a TUI learns to re-layout. Applies to both modes; only buffer
        mode has a screen buffer to resize.
        """
        self.process.setwinsize(height, width)
        self.dimensions = (width, height)
        time.sleep(delay)
        if self.mode == "buffer":
            self.screen.resize(height, width)
            self._update_buffer()

    def send(self, keys: str, delay: float = 0.3):
        """Send keys to the process and wait for them to be processed."""
        self.assert_alive()
        self.process.send(keys)
        time.sleep(delay)
        if self.mode == "buffer":
            self._update_buffer()

    def capture(self, preserve_format: bool = False) -> ScreenModel:
        """Return a snapshot of the current terminal state.

        A dead child is reported for a buffer-mode session (its grid is stale);
        a stream session still returns the accumulated output, which is what a
        finished CLI command leaves behind.

        Args:
            preserve_format: If True, keep trailing whitespace and ANSI codes
                             for an accurate picture of the terminal layout.

        """
        if self.mode == "buffer":
            self.assert_alive()
            self._update_buffer()
            rendered = screen_lines(self.screen)
            lines = tuple(rendered if preserve_format else [line.rstrip() for line in rendered])
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
# Keeps two screenshots taken in the same second from overwriting each other.
_artifact_counter = itertools.count(1)
sessions: dict[str, ScreenSession] = {}


SPECIAL_KEYS: dict[str, str] = {
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "page_up": "\x1b[5~",
    "page_down": "\x1b[6~",
    "insert": "\x1b[2~",
    "delete": "\x1b[3~",
    "backspace": "\x7f",
    "escape": "\x1b",
    "enter": "\r",
    "tab": "\t",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}


def _parse_dimensions(dimensions: str) -> tuple[int, int]:
    """Parse a `WxH` terminal size, or raise ToolError explaining the format.

    The terminal ioctl packs both axes as unsigned shorts; an out-of-range value
    raises deep inside pexpect and has been observed to block the call for tens
    of seconds before it does, so the range is checked here.
    """
    try:
        width, height = map(int, dimensions.split("x"))
    except (ValueError, AttributeError):
        raise ToolError(
            f"Invalid dimensions format: '{dimensions}'. Expected format like '80x24' (WxH).",
        )
    if not 1 <= width <= 65535 or not 1 <= height <= 65535:
        raise ToolError(
            f"Invalid dimensions: '{dimensions}'. Width and height must be between 1 and 65535.",
        )
    return width, height


def _default_artifact_path(session_id: str, suffix: str) -> Path:
    """A collision-free path under the system temp dir for a screenshot or GIF."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id).strip("-") or "session"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "mcp-tui-test" / f"{safe}-{stamp}-{next(_artifact_counter)}.{suffix}"


@mcp.tool
def launch_tui(
    command: str,
    session_id: str = "default",
    timeout: int = 30,
    dimensions: str | None = "80x24",
    mode: str = "buffer",
    env: dict[str, str] | None = None,
) -> str:
    """Launch a TUI app in a subprocess for automated testing.

    Call this first — creates a session that all other tools reference.
    **Buffer mode** (default): full terminal emulation via pyte, position-aware assertions.
    **Stream mode**: line-by-line pexpect accumulators for CLI tools.

    Args:
        command: Program and arguments to run (e.g. "python my_app.py"). It is exec'd directly,
                 not through a shell, so `;`, `|`, `>` and `$(…)` reach the program as literal
                 arguments — wrap it in `sh -c '…'` when you need shell semantics.
        session_id: Label for this session (default: "default")
        timeout: Kill subprocess after N seconds (default: 30)
        dimensions: Terminal size as WxH, e.g. "80x24" (default: "80x24")
        mode: "buffer" for full TUIs, "stream" for line-by-line CLI (default: "buffer")
        env: Environment variables to add for the child process. Inherited values are kept, these
             override them — use it to pin TERM or LC_ALL when the caller's environment would make a
             full-screen app render garbage or localize its labels.

    Returns:
        Confirmation with session ID, command, dimensions, and mode

    """
    # Parse dimensions
    width, height = _parse_dimensions(dimensions) if dimensions else (80, 24)

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
        env={**os.environ, **env} if env else None,
    )

    # Launch the TUI application session
    session = ScreenSession(process=process, dimensions=(width, height), mode=mode, session_id=session_id)
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
    session.assert_alive()
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
            session.assert_alive()
            session._update_buffer()
            display = screen_lines(session.screen)
            if any(compiled.search(line) for line in display):
                return f"✓ Found expected text: '{pattern}'"
            time.sleep(0.1)
        # Fallback: check raw pexpect buffer (ANSI-stripped) for patterns
        # that pyte may not have tracked correctly
        raw = (session.process.before or "") + (getattr(session.process, "after", None) or "")
        raw_clean = strip_ansi(raw)
        if compiled.search(raw_clean):
            session._update_buffer()
            return f"✓ Found expected text: '{pattern}' (from raw output)"
        model = session.capture()
        dump = "\n".join(model.lines[:40])
        raise ToolError(
            f"Pattern '{pattern}' not found after {timeout}s.\nCurrent screen ({len(model.lines)} lines):\n{dump}",
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
            f"Pattern '{pattern}' not found after {timeout}s.\nCurrent screen ({len(model.lines)} lines):\n{dump}",
        )
    model = session.capture()
    dump = "\n".join(model.lines[:40])
    raise ToolError(
        f"EOF reached before finding: '{pattern}'\nLast screen ({len(model.lines)} lines):\n{dump}",
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

    Columns are cell columns: a wide glyph (emoji flag, CJK) occupies its own
    column and a filler space occupies the next one, so the column of every
    character to its right is still the column the terminal used.

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
def screenshot(
    path: str | None = None,
    session_id: str = "default",
    font_size: int = 14,
    cursor: bool = True,
) -> str:
    """Save a pixel screenshot of the current screen as a PNG (buffer mode only).

    The text tools describe what the screen *says*; this is the only witness of
    what it *looks like* — colour, contrast, colour-only state (a dimmed/disabled
    row, a selection highlight) and the cursor. It draws the same pyte cell grid
    the text reads use, so the picture and the transcript cannot disagree.

    Read the written file with your image/view tool; use it before judging a
    palette, a "disabled" cue, or any state that the text alone cannot show.

    Args:
        path: Where to write the PNG. Defaults to a timestamped file under the
              system temp directory (`mcp-tui-test/<session>-<timestamp>.png`).
        session_id: Session identifier (default: "default")
        font_size: Font size in points, 4–96 (default: 14)
        cursor: Paint the cursor as an inverted block (default: True) — the only
                visual witness of which field has focus.

    Returns:
        Confirmation with the absolute PNG path and its pixel size

    Raises:
        ToolError: Stream-mode session, bad font size, or the child has exited

    """
    session = require_session(session_id)
    if session.mode != "buffer":
        raise ToolError(
            "screenshot needs buffer mode: a stream-mode session has no cell grid to draw. "
            "Re-launch with mode='buffer'.",
        )
    if not FONT_SIZE_MIN <= font_size <= FONT_SIZE_MAX:
        raise ToolError(f"font_size must be between {FONT_SIZE_MIN} and {FONT_SIZE_MAX}, got {font_size}.")
    model = session.capture(preserve_format=True)
    target = Path(path) if path else _default_artifact_path(session_id, "png")
    try:
        written = render_screen_png(
            session.screen,
            target,
            font_size=font_size,
            show_cursor=cursor,
        )
    except RuntimeError as exc:
        raise ToolError(str(exc))
    chars = f"{model.width}x{model.height} cells, font {font_size}pt"
    palette = describe_screen_attributes(session.screen)
    return f"✓ Screenshot saved: {Path(written).resolve()}\n{chars}, {palette}"


@mcp.tool
def gif(
    frames: list[str],
    path: str | None = None,
    duration_ms: int = 500,
    loop: int = 0,
) -> str:
    """Stitch PNG screenshots into an animated GIF ("movie" of a session).

    Take the frames with `screenshot(path=…)`, then pass their paths here. Frame
    order is the order you list them. Frames are resized onto the first frame's
    canvas, so a session that changed size mid-run still animates.

    Args:
        frames: Two or more PNG paths, in play order (from `screenshot`)
        path: Where to write the GIF. Defaults to a timestamped file under the
              system temp directory.
        duration_ms: Milliseconds each frame is shown (default: 500, minimum 1)
        loop: 0 loops forever, N plays N+1 times (default: 0)

    Returns:
        Confirmation with the absolute GIF path

    Raises:
        ToolError: Fewer than two frames, a missing frame file, or a bad duration

    """
    if len(frames) < 2:
        raise ToolError(f"gif needs at least 2 frames, got {len(frames)}; take them with screenshot().")
    if duration_ms < 1:
        raise ToolError(f"duration_ms must be >= 1, got {duration_ms}.")
    missing = [frame for frame in frames if not Path(frame).is_file()]
    if missing:
        raise ToolError(f"frame file(s) not found: {', '.join(missing)}")
    target = Path(path) if path else _default_artifact_path("session", "gif")
    try:
        written = frames_to_gif(frames, target, duration_ms=duration_ms, loop=loop)
    except RuntimeError as exc:
        raise ToolError(str(exc))
    except ValueError as exc:
        raise ToolError(str(exc))
    return f"✓ GIF saved: {Path(written).resolve()}\n{len(frames)} frames @ {duration_ms}ms"


@mcp.tool
def get_cell_style(
    row: int,
    col: int,
    session_id: str = "default",
) -> str:
    """Read one cell's colour and text attributes (buffer mode only).

    The text tools answer *what* the screen says; this answers *how* it says it —
    the per-cell fg/bg plus bold, reverse, italic, underline, strike and blink.
    Use it to make a colour-only claim assertable in text form (a red failure
    marker, a dimmed/disabled row, a selection highlight) instead of eyeballing a
    screenshot.

    Colours are pyte's own encoding: a 16-colour name (`red`, `brightgreen`), a
    6-digit hex string for a 256-colour or true-colour value (`ff0000`), or
    `default` when the app set none.

    Args:
        row: 0-indexed row number
        col: 0-indexed column number
        session_id: Session identifier (default: "default")

    Returns:
        The cell's character and attributes; out-of-bounds reads return defaults

    Raises:
        ToolError: Stream-mode session (no cell grid), or the child has exited

    """
    session = require_session(session_id)
    if session.mode != "buffer":
        raise ToolError(
            "get_cell_style needs buffer mode: a stream-mode session has no cell grid. "
            "Re-launch with mode='buffer'.",
        )
    model = session.capture(preserve_format=True)
    if row >= len(model.lines) or col >= model.width:
        return f"Style at ({row}, {col}) (session: {session_id}): char=' ' fg=default bg=default (out of bounds)"

    cell = session.screen.buffer[row][col]
    char = cell.data or " "
    return (
        f"Style at ({row}, {col}) (session: {session_id}): char={char!r} "
        f"fg={cell.fg or 'default'} bg={cell.bg or 'default'} "
        f"bold={'yes' if cell.bold else 'no'} reverse={'yes' if cell.reverse else 'no'} "
        f"italic={'yes' if cell.italics else 'no'} underline={'yes' if cell.underscore else 'no'} "
        f"strike={'yes' if cell.strikethrough else 'no'} blink={'yes' if cell.blink else 'no'}"
    )


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
            f'  {sid} — {session.mode} (PID {pid}, {w}x{h}, up {uptime:.0f}s) — "{preview}"',
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


@mcp.tool
def send_special_keys(
    keys: list[str],
    session_id: str = "default",
    delay: float = 0.3,
) -> str:
    """Send special keys by name (arrows, function keys, etc.) to the TUI application.

    Provides a declarative way to send keys that would otherwise require
    raw escape sequences. Each element in `keys` is a named special key
    mapped to its terminal escape sequence automatically.

    Supported key names:
    Arrow keys: up, down, left, right
    Navigation: home, end, page_up, page_down
    Editing: backspace, delete, insert
    Control: enter, tab, escape
    Function keys: f1, f2, ..., f12

    For regular text and simple control characters, use send_keys instead.
    For Ctrl+letter combinations, use send_ctrl.

    Examples:
      send_special_keys(keys=["down", "down", "enter"])
      send_special_keys(keys=["escape", "q"])
      send_special_keys(keys=["f5"])  # Refresh in many TUIs

    Args:
        keys: List of special key names to send in sequence.
        session_id: Session identifier (default: "default")
        delay: Seconds to wait after sending for the app to process (default: 0.3, minimum 0.1)

    Returns:
        Confirmation message

    Raises:
        ToolError: If any key name is not recognized, or session not found.
    """
    delay = max(delay, 0.1)
    session = require_session(session_id)
    session.assert_alive()
    unknown = [k for k in keys if k not in SPECIAL_KEYS]
    if unknown:
        valid = ", ".join(sorted(SPECIAL_KEYS))
        raise ToolError(f"Unknown special key(s): {', '.join(unknown)}. Valid keys: {valid}")

    encoded = "".join(SPECIAL_KEYS[k] for k in keys)
    session.process.send(encoded)
    time.sleep(delay)
    if session.mode == "buffer":
        session._update_buffer()

    return f"✓ Sent {len(keys)} special key(s) to session {session_id}"


@mcp.tool
def resize(
    dimensions: str,
    session_id: str = "default",
    delay: float = 0.3,
) -> str:
    """Resize a live session's terminal, and let the app re-layout.

    Sets the pseudo-terminal size, so the kernel delivers SIGWINCH to the child
    (that is how a TUI learns to re-layout) and the emulated screen follows.
    The responsiveness path that only a resize reaches — a layout that switches
    below N rows/columns — cannot be driven any other way in a headless run.

    Args:
        dimensions: New terminal size as WxH, e.g. "80x20"
        session_id: Session identifier (default: "default")
        delay: Seconds to wait for the repaint after the resize (default: 0.3)

    Returns:
        Confirmation with the new size

    Raises:
        ToolError: Bad dimensions, or the session's child has exited

    """
    session = require_session(session_id)
    width, height = _parse_dimensions(dimensions)
    session.assert_alive()
    session.resize(width, height, delay=max(delay, 0.0))
    return f"✓ Resized session {session_id} to {width}x{height}"


# ---------------------------------------------------------------------------
# Scenario execution
# ---------------------------------------------------------------------------

PINNED_TERM = "xterm-256color"
PINNED_LOCALE = "C"

# Tools that take no session_id; every other handler receives the scenario's session.
SESSIONLESS_TOOLS = frozenset({"list_sessions", "gif"})

_SCENARIO_HANDLERS: dict[str, Callable[..., str]] = {
    "launch_tui": launch_tui,
    "send_keys": send_keys,
    "send_ctrl": send_ctrl,
    "send_special_keys": send_special_keys,
    "expect_text": expect_text,
    "assert_contains": assert_contains,
    "assert_at_position": assert_at_position,
    "get_line": get_line,
    "get_screen_region": get_screen_region,
    "get_char": get_char,
    "get_cell_style": get_cell_style,
    "get_cursor_position": get_cursor_position,
    "capture_screen": capture_screen,
    "wait_for_stable": wait_for_stable,
    "wait_for_change": wait_for_change,
    "get_session_info": get_session_info,
    "list_sessions": list_sessions,
    "close_session": close_session,
    "screenshot": screenshot,
    "gif": gif,
    "resize": resize,
}

def _pinned_child_env(step_env: object, pin_env: bool) -> dict[str, str] | None:
    """Environment additions for a launched child.

    Walkthroughs have failed silently when the caller's TERM was `dumb` (garbled screen, still a `✓`)
    or the host locale leaked into assertions, so a scenario pins sane values unless the step's own
    `env=` overrides them.
    """
    pinned: dict[str, str] = {}
    if pin_env:
        if os.environ.get("TERM", "") in ("", "dumb"):
            pinned["TERM"] = PINNED_TERM
        pinned.update(dict.fromkeys(("LC_ALL", "LANG", "LANGUAGE"), PINNED_LOCALE))
    if isinstance(step_env, dict):
        pinned.update({str(key): str(value) for key, value in step_env.items()})
    return pinned or None


def _diagnostic_payload(diagnostics: list[Diagnostic]) -> list[dict]:
    return [asdict(diagnostic) for diagnostic in diagnostics]


def _header_line(plan) -> list[str]:
    """Title a report with the document's own header, when it has one."""
    fields = dict(plan.header)
    if not fields:
        return []
    name = one_line(fields.get("name", ""))
    description = one_line(fields.get("description", ""))
    return [f"Scenario document: {name or '(unnamed)'} — {description}" if description else f"Scenario document: {name}"]


def _run_choice(step, session_id: str) -> dict[str, object]:
    """Evaluate every alternative of a choice() epilogue and collect the labels that matched.

    An assertion that does not hold is a *no match*, not a failure: that is what makes the epilogue
    able to report "error found" as a legitimate outcome. Every alternative is evaluated, so labels
    that are true at the same time are all reported.
    """
    evaluated: list[dict] = []
    matched: list[str] = []
    for label, alternative in step.alternatives:
        arguments: dict = dict(alternative.arguments)
        if alternative.tool not in SESSIONLESS_TOOLS:
            arguments["session_id"] = session_id
        started = time.monotonic()
        try:
            text = str(_SCENARIO_HANDLERS[alternative.tool](**arguments))
            status = "matched"
            matched.append(label)
        except ToolError as exc:
            text, status = f"{exc}", "no match"
        except Exception as exc:  # an unexpected error is not a mismatch; keep it visible
            text, status = f"{type(exc).__name__}: {exc}", "error"
        evaluated.append(
            {
                "label": label,
                "step": alternative.name,
                "tool": alternative.tool,
                "status": status,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "output": text,
            }
        )
    return {"matched": matched, "evaluated": evaluated, "status": "ok" if matched else "no match"}


@mcp.tool
def run_scenario(
    script: str | None = None,
    ad_hoc: str | None = None,
    session_id: str = "default",
    dry_run: bool = False,
    stop_on_error: bool = True,
    keep_alive: bool | None = None,
    pin_env: bool = True,
) -> ToolResult:
    r"""Parse, lint, and optionally execute a Python-like scenario.

    Two mutually exclusive inputs:

    - `script=` a **markdown scenario document**: a `---` header with `name` and `description` (plus
      optional `app`, `tags`), prose, and **exactly one** ```python block holding the steps. Other
      fenced blocks are documentation and are ignored; diagnostics point at the document's own lines.
    - `ad_hoc=` raw step calls and nothing else — no header, no fences. For iterating before the
      scenario is written down.

    A scenario is a sequence of bare step calls::

        launch("env TERM=xterm-256color LC_ALL=C nano notes.txt", mode="buffer", dimensions="120x40")
        expect("GNU nano", timeout=5)
        send("hello", delay=0.5)
        assert_at("hello", row=1, col=0)
        close()

    It ends with an optional `choice()` epilogue — the last instruction, and the only place an
    instruction may appear inside another call::

        choice({
            "saved cleanly": expect("Wrote 1 line", timeout=5),
            "write failed": expect("Error writing", timeout=2),
            "still dirty": assert_text(" *"),
        })

    Every alternative is evaluated in written order (so their waits add up) and every label that
    matched is reported; if none matched the step fails, so an unexplained screen cannot pass as a
    success. Allowed inside `choice()`: `expect`, `assert_text`, `assert_at`.

    Steps map 1:1 onto the server's tools (`launch`→launch_tui, `send`→send_keys, `line`→get_line,
    `key`→send_special_keys, …). The script source is parsed with `ast` and **never executed**: every
    argument must be a literal, so the resolved pipeline is exactly what runs, and it can be replayed
    verbatim. Anything else (assignments, imports, loops, f-strings, computed arguments) is a lint
    error rather than a surprise at runtime.

    Blast radius: the script is parsed, never executed — but a `launch` step runs an arbitrary
    program as the server's user (exactly what `launch_tui` does) and a scenario is not sandboxed.
    Treat a scenario document from an untrusted source the way you would treat a shell script.

    Lint errors abort before anything is launched. Warnings are advisory — `send` keys containing
    `\n` (raw-mode TUIs want `\r`), buffer-only reads on a stream-mode launch, a missing `close()`,
    a `change()` reference copied from a framed capture.

    Args:
        script: Markdown scenario document (header + exactly one ```python block).
        ad_hoc: Raw scenario source, parsed as-is (no header, no fences).
        session_id: Session the steps run against. Applied to every step; `session_id` is not
                    accepted inside the script, and it must not be empty (launch_tui auto-generates
                    ids only when it is called directly).
        dry_run: Lint and resolve the pipeline without touching a session.
        stop_on_error: Stop at the first failing step instead of running the rest.
        keep_alive: Leave the session running after the run. True never closes it; False closes it
                    even when attaching (that is an explicit instruction); None — the default — closes
                    only a session this call launched, so an attached session survives for a follow-up
                    capture.
        pin_env: Pin TERM/LC_ALL/LANG/LANGUAGE for launched children (a step's `env=` wins; set False
                 when the scenario is about localization or inherited environment).

    Returns:
        ToolResult with a rendered report (caret diagnostics, resolved pipeline, transcript) as text
        and the machine-readable fields in structured content: ok, action, diagnostics, pipeline,
        results, session_alive, rescue_files.
    """
    attached = session_id in sessions
    if (script is None) == (ad_hoc is None):
        mode_problem = Diagnostic(
            "error",
            "E_INPUT_MODE",
            1,
            0,
            "pass exactly one of script= (markdown document) or ad_hoc= (raw script)",
            "script= expects a header plus one ```python block; ad_hoc= is the bare step list",
        )
        plan = Plan(diagnostics=(mode_problem,))
    elif script is not None:
        plan = parse_document(script, special_keys=frozenset(SPECIAL_KEYS))
    else:
        plan = parse_script(ad_hoc or "", special_keys=frozenset(SPECIAL_KEYS))
    document = script if script is not None else (ad_hoc or "")

    diagnostics = list(plan.diagnostics)
    if not session_id:
        diagnostics.append(
            Diagnostic(
                "error",
                "E_BAD_SESSION_ID",
                1,
                0,
                "session_id must not be empty",
                "pass the scenario's label, or omit the argument to use 'default'; launch_tui only "
                "auto-generates ids when it is called directly",
            )
        )
    if (
        not plan.errors
        and not any(diagnostic.is_error for diagnostic in diagnostics)
        and not dry_run
        and not attached
        and not plan.launches
    ):
        diagnostics.append(
            Diagnostic(
                "error",
                "E_NO_SESSION",
                1,
                0,
                f"no live session '{one_line(session_id)}' and the script has no launch() step",
                "inspect list_sessions() or add launch(...) as the first step",
            )
        )
    diagnostics.sort(key=lambda diagnostic: (diagnostic.line, diagnostic.col, diagnostic.code))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.is_error]

    if dry_run or errors:
        action = "linted" if errors else "dry_run"
        sections = _header_line(plan) + []
        if diagnostics:
            sections.append(render_diagnostics(diagnostics, document))
        if plan.steps:
            sections.append("Resolved pipeline:\n" + render_pipeline(plan.steps))
        if errors:
            sections.append(f"Nothing was launched: {len(errors)} lint error(s).")
        else:
            sections.append("Dry run: nothing was launched and no session was touched.")
        payload = {
            "ok": not errors,
            "action": action,
            "session_id": session_id,
            "attached": attached,
            # Nothing ran, so nothing was kept alive: the session is simply untouched.
            "kept_alive": False,
            "header": dict(plan.header),
            "diagnostics": _diagnostic_payload(diagnostics),
            "pipeline": plan.payload(),
            "results": [],
            "session_alive": attached,
            "rescue_files": [],
            "choice": None,
        }
        return ToolResult(content=[TextContent(type="text", text="\n\n".join(sections))], structured_content=payload)

    keep_open = attached if keep_alive is None else keep_alive
    results: list[dict] = []
    step_errors = 0
    choice_verdict: dict[str, object] | None = None
    owned = False  # set only once a launch step actually created or replaced the session
    # Snapshot rescue-file state before the run: file mtimes can lag the wall clock by a tick, so a
    # timestamp comparison is not a reliable "was this written during the run?" test. An attached
    # session contributes its own launch command, otherwise a script that only reads would never
    # watch the fixture it is about to modify.
    watched_commands = [str(step.arguments.get("command", "")) for step in plan.steps if step.tool == "launch_tui"]
    if attached:
        watched_commands.append(sessions[session_id].command)
    watch_dirs = rescue_dirs(watched_commands, set())
    saves_before = save_files(watch_dirs)

    try:
        for index, step in enumerate(plan.steps, start=1):
            arguments: dict = dict(step.arguments)
            if step.tool == "launch_tui":
                arguments["env"] = _pinned_child_env(arguments.get("env"), pin_env)
                arguments["session_id"] = session_id
            elif step.tool not in SESSIONLESS_TOOLS:
                arguments["session_id"] = session_id
            if step.tool == "choice":
                choice_verdict = _run_choice(step, session_id)
                matched = [str(label) for label in choice_verdict["matched"]]
                status = "ok" if matched else "error"
                output = (
                    "matched: " + ", ".join(repr(one_line(label)) for label in matched)
                    if matched
                    else "no alternative matched"
                )
                if status == "error":
                    step_errors += 1
                results.append(
                    {
                        "n": index,
                        "step": step.name,
                        "tool": step.tool,
                        "status": status,
                        "elapsed_ms": sum(int(record["elapsed_ms"]) for record in choice_verdict["evaluated"]),
                        "output": output,
                        "choice": choice_verdict,
                    }
                )
                if status == "error" and stop_on_error:
                    break
                continue
            step_started = time.monotonic()
            try:
                output = str(_SCENARIO_HANDLERS[step.tool](**arguments))
                status = "ok"
                if step.tool == "launch_tui":
                    owned = True  # the session now belongs to this call
            except ToolError as exc:
                output, status = f"ToolError: {exc}", "error"
            except Exception as exc:  # unexpected failures are recorded, not propagated
                output, status = f"{type(exc).__name__}: {exc}", "error"
            if status == "error":
                step_errors += 1
            results.append(
                {
                    "n": index,
                    "step": step.name,
                    "tool": step.tool,
                    "status": status,
                    "elapsed_ms": round((time.monotonic() - step_started) * 1000),
                    "output": output,
                }
            )
            if status == "error" and stop_on_error:
                break
    finally:
        # Close only what this call is responsible for: a session it launched, or — when keep_alive is
        # explicitly False — one it actually drove steps through. A failed launch must never kill an
        # attached session this call did not create, and pop() keeps concurrent runs from racing on
        # `del sessions[...]`.
        drove_steps = any(record["status"] == "ok" for record in results)
        if not keep_open and session_id in sessions and (owned or drove_steps):
            session = sessions.pop(session_id, None)
            if session is not None:
                try:
                    session.close()
                except Exception:  # teardown must not mask the run result
                    pass

    rescues = new_save_files(watch_dirs, saves_before)
    session_alive = session_id in sessions
    payload = {
        "ok": step_errors == 0,
        "action": "executed",
        "session_id": session_id,
        "attached": attached,
        "kept_alive": keep_open and session_alive,
        "header": dict(plan.header),
        "diagnostics": _diagnostic_payload(diagnostics),
        "pipeline": plan.payload(),
        "results": results,
        "session_alive": session_alive,
        "rescue_files": rescues,
        "choice": choice_verdict,
    }
    sections = _header_line(plan)
    if diagnostics:
        sections.append(render_diagnostics(diagnostics, document))
    sections.append(
        render_execution(
            action="executed" + (" (session kept alive)" if payload["kept_alive"] else ""),
            session_id=session_id,
            attached=attached,
            session_alive=session_alive,
            results=results,
            rescues=rescues,
            errors=step_errors,
        )
    )
    return ToolResult(content=[TextContent(type="text", text="\n\n".join(sections))], structured_content=payload)


if __name__ == "__main__":
    mcp.run()

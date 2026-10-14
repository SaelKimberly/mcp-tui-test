"""Helper functions for screen polling, detection, and terminal emulation drain.

Extracted from _server.py to keep tool definitions clean.
"""

import re
import time

import pexpect

# Compile ANSI escape regex once for performance
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    return ANSI_ESCAPE.sub("", text)


def drain_buffer(process, screen, stream, max_passes: int = 3, max_reads: int = 100) -> None:
    """Drain all available process output into a pyte screen buffer.

    Uses multiple short-drain passes with sleeps between to catch
    staggered or batched output that a single pass might miss.

    SAFETY: `max_reads` caps the inner while-loop to prevent an
    indefinite hang when a continuously-outputting TUI keeps the
    ptty buffer non-empty (select(0) always returns True).

    Args:
        process: pexpect.spawn-like object with read_nonblocking()
        screen: pyte.Screen instance
        stream: pyte.Stream instance
        max_passes: Number of drain passes (default: 3)
        max_reads: Max read_nonblocking calls per pass (default: 100)

    """
    for _ in range(max_passes):
        reads = 0
        try:
            while reads < max_reads:
                output = process.read_nonblocking(size=8192, timeout=0)
                if not output:
                    break
                stream.feed(output)
                reads += 1
        except pexpect.TIMEOUT:
            pass  # No more output ready on this pass
        except pexpect.EOF:
            return  # Process ended — no point retrying
        time.sleep(0.1)


def screen_text_changed(a: str, b: str, threshold: float = 0.05) -> bool:
    """Compare two screen texts, return True if difference exceeds threshold.

    Splits on newlines and counts non-matching non-empty lines.
    Returns True when mismatches / max(len(a), len(b), 1) > threshold.
    """
    a_lines = [l for l in a.split("\n") if l.strip()]
    b_lines = [l for l in b.split("\n") if l.strip()]
    if not a_lines and not b_lines:
        return False
    max_len = max(len(a_lines), len(b_lines), 1)
    mismatches = 0
    for i in range(max_len):
        line_a = a_lines[i] if i < len(a_lines) else ""
        line_b = b_lines[i] if i < len(b_lines) else ""
        if line_a != line_b and (line_a.strip() or line_b.strip()):
            mismatches += 1
    return mismatches / max_len > threshold


def poll_until_stable(
    session,
    timeout: float = 10,
    interval: float = 0.1,
    consecutive: int = 3,
) -> str:
    """Poll screen until it remains stable for `consecutive` samples.

    Calls session.capture() each interval. When `consecutive` identical
    texts are seen, returns the stable text. Raises TimeoutError if
    screen does not stabilize within `timeout` seconds.

    Edge: if session returns empty text on all samples (consecutive),
    that IS stable — returns empty string.
    """
    deadline = time.monotonic() + timeout
    stable_count = 0
    last_text = ""

    while time.monotonic() < deadline:
        model = session.capture()
        text = model.text
        if text == last_text:
            stable_count += 1
            if stable_count >= consecutive:
                return text
        else:
            stable_count = 0
        last_text = text
        # Never sleep past the deadline: a large interval used to overshoot the timeout entirely.
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    raise TimeoutError(f"Screen did not stabilize within {timeout}s")


def poll_until_changed(
    session,
    reference: str,
    timeout: float = 10,
    interval: float = 0.1,
) -> str:
    """Poll screen until content differs from `reference`.

    Uses screen_text_changed for comparison. Returns the new text.
    Raises TimeoutError if no change detected within `timeout`.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        model = session.capture()
        text = model.text
        if screen_text_changed(reference, text):
            return text
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    raise TimeoutError(f"Screen did not change from reference within {timeout}s")

"""Unit tests for _screen.py helper functions (DummyProcess, no real subprocesses)."""

import pytest

from mcp_tui_test._screen import (
    drain_buffer,
    poll_until_changed,
    poll_until_stable,
    screen_lines,
    screen_text_changed,
    strip_ansi,
)
from mcp_tui_test._server import ScreenSession
from tests.conftest import DummyProcess


def _screen_with(columns: int = 12, lines: int = 2):
    """A pyte screen fed with raw escape output."""
    import pyte

    screen = pyte.Screen(columns, lines)
    stream = pyte.Stream(screen)
    return screen, stream


class TestScreenLines:
    def test_plain_text_per_row(self):
        screen, stream = _screen_with()
        stream.feed("hello\r\nworld")
        lines = screen_lines(screen)
        assert lines[0] == "hello       "
        assert lines[1] == "world       "

    def test_every_row_is_exactly_columns_wide(self):
        screen, stream = _screen_with(columns=20)
        stream.feed("ab\U0001F3F4cd")
        assert all(len(line) == 20 for line in screen_lines(screen))

    def test_wide_glyph_keeps_following_columns(self):
        """A wide glyph occupies its own column plus a filler space, so a cell's
        string index is the column the terminal used."""
        screen, stream = _screen_with(columns=12)
        stream.feed("a\U0001F3F4b")
        line = screen_lines(screen)[0]
        assert line[0] == "a"
        assert line[1] == "\U0001F3F4"
        assert line[2] == " "
        assert line[3] == "b"

    def test_stale_wide_stub_does_not_crash(self):
        """pyte's own Screen.display raises IndexError here; screen_lines must not."""
        import pyte

        screen, stream = _screen_with(columns=12)
        stream.feed("\U0001F3F4")  # wide glyph leaves a stub cell at column 1
        stream.feed("\x1b[1;1Hx")  # overwrite column 0 with a narrow glyph
        with pytest.raises(IndexError):
            pyte.Screen.display.fget(screen)  # type: ignore[attr-defined]
        line = screen_lines(screen)[0]
        assert line[0] == "x"
        assert len(line) == 12


class TestStripAnsi:
    def test_strips_simple_ansi(self):
        assert strip_ansi("\x1b[32mgreen\x1b[0m") == "green"

    def test_no_ansi_unchanged(self):
        assert strip_ansi("hello world") == "hello world"

    def test_empty_string(self):
        assert strip_ansi("") == ""


class TestScreenTextChanged:
    def test_identical_strings(self):
        assert screen_text_changed("hello\nworld", "hello\nworld") is False

    def test_completely_different(self):
        assert screen_text_changed("hello", "goodbye") is True

    def test_one_line_changed_out_of_twenty(self):
        a = "\n".join([f"line {i}" for i in range(20)])
        b = "\n".join([f"line {i}" if i != 5 else "CHANGED" for i in range(20)])
        # 1/20 = 0.05, default threshold is 0.05, so 0.05 > 0.05 is False
        assert screen_text_changed(a, b) is False

    def test_one_line_changed_out_of_hundred(self):
        a = "\n".join([f"line {i}" for i in range(100)])
        b = "\n".join([f"line {i}" if i != 50 else "CHANGED" for i in range(100)])
        # 1/100 = 0.01 < 0.05
        assert screen_text_changed(a, b) is False

    def test_two_lines_changed_out_of_twenty(self):
        a = "\n".join([f"line {i}" for i in range(20)])
        b = "\n".join(
            [f"line {i}" if i not in (5, 10) else "CHANGED" for i in range(20)],
        )
        # 2/20 = 0.10 > 0.05
        assert screen_text_changed(a, b) is True

    def test_both_empty_returns_false(self):
        assert screen_text_changed("", "") is False

    def test_only_blank_lines_returns_false(self):
        assert screen_text_changed("\n\n  \n", "\n\t\n") is False

    def test_one_side_empty(self):
        a = "some content\nhere"
        assert screen_text_changed(a, "") is True
        assert screen_text_changed("", a) is True

    def test_custom_threshold(self):
        a = "\n".join([f"line {i}" for i in range(10)])
        b = "\n".join(
            [f"line {i}" if i != 5 else "CHANGED" for i in range(10)],
        )
        # 1/10 = 0.10 < 0.20
        assert screen_text_changed(a, b, threshold=0.20) is False
        # 1/10 = 0.10 > 0.05
        assert screen_text_changed(a, b, threshold=0.05) is True


class TestPollUntilStable:
    def test_immediately_stable(self):
        """DummyProcess with stable before text — returns after consecutive polls."""
        proc = DummyProcess(before="stable content\nline 2\n", after="")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        text = poll_until_stable(session, timeout=5, interval=0.01, consecutive=2)
        assert "stable content" in text

    def test_stabilizes_after_change(self):
        """Polling with a session whose before text changes then stops."""
        session = ScreenSession(
            process=DummyProcess(before="initial\nstate\n", after=""),
            dimensions=(80, 24),
            mode="stream",
        )
        text = poll_until_stable(session, timeout=5, interval=0.01, consecutive=2)
        assert "initial" in text


class TestPollUntilChanged:
    def test_immediately_different(self):
        """Reference differs from session output — returns immediately."""
        proc = DummyProcess(before="new content\nline 2\n", after="")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        text = poll_until_changed(session, "old reference", timeout=5, interval=0.01)
        assert "new content" in text

    def test_no_change_raises_timeout(self):
        """Reference matches session output — raises TimeoutError."""
        proc = DummyProcess(before="same old\n", after="")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        with pytest.raises(TimeoutError):
            poll_until_changed(session, "same old", timeout=0.1, interval=0.01)


class TestDrainBuffer:
    def test_closed_process_handles_eof(self):
        """DummyProcess closed — handles EOF gracefully."""
        proc = DummyProcess(before="", after="")
        proc._closed = True
        import pyte

        screen = pyte.Screen(80, 24)
        stream = pyte.Stream(screen)
        # Should not raise
        drain_buffer(proc, screen, stream, max_passes=1)

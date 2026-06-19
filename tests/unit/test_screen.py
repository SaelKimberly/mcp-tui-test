"""Unit tests for _screen.py helper functions (DummyProcess, no real subprocesses)."""

import pytest
from mcp_tui_test._screen import (
    drain_buffer,
    poll_until_changed,
    poll_until_stable,
    screen_text_changed,
    strip_ansi,
)
from mcp_tui_test._server import ScreenSession
from tests.conftest import DummyProcess


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
            [f"line {i}" if i not in (5, 10) else "CHANGED" for i in range(20)]
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
            [f"line {i}" if i != 5 else "CHANGED" for i in range(10)]
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

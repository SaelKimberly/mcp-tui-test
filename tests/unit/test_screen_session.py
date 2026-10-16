"""Unit tests for ScreenSession (DummyProcess, no real subprocesses)."""

import pytest
from fastmcp.exceptions import ToolError

from mcp_tui_test._server import CursorPosition, ScreenModel, ScreenSession
from tests.conftest import DummyProcess


class TestInit:
    def test_stream_mode(self, dummy_process):
        session = ScreenSession(process=dummy_process, dimensions=(80, 24), mode="stream")
        assert session.mode == "stream"
        assert session.dimensions == (80, 24)
        assert session.process is dummy_process
        assert not hasattr(session, "screen")

    def test_buffer_mode(self, dummy_process):
        session = ScreenSession(process=dummy_process, dimensions=(80, 24), mode="buffer")
        assert session.mode == "buffer"
        assert hasattr(session, "screen")
        assert hasattr(session, "stream")

    def test_launch_time_set(self, dummy_process):
        session = ScreenSession(
            process=dummy_process,
            dimensions=(80, 24),
            mode="stream",
        )
        assert session.launch_time > 0
        assert isinstance(session.launch_time, float)


class TestSend:
    def test_appends_keys(self, session):
        session.send("abc")
        assert session.process.sent == ["abc"]

    def test_multiple_sends(self, session):
        session.send("a")
        session.send("b")
        assert session.process.sent == ["a", "b"]


class TestCapture:
    def test_stream_returns_screen_model(self, session):
        session.process.before = "hello\nworld\n"
        model = session.capture()
        assert isinstance(model, ScreenModel)
        assert model.mode == "stream"
        assert "hello" in model.text

    def test_stream_includes_after(self, session):
        session.process.before = "menu:\n> "
        session.process.after = "Hello"
        model = session.capture()
        assert "Hello" in model.text

    def test_stream_empty_before_after(self, session):
        session.process.before = ""
        session.process.after = ""
        model = session.capture()
        assert model.text == ""

    def test_stream_strips_ansi(self, session):
        session.process.before = "\x1b[32mgreen\x1b[0m\n"
        model = session.capture()
        assert "green" in model.text
        assert "\x1b" not in model.text

    def test_buffer_returns_screen_model(self):
        proc = DummyProcess(before="line1\nline2\n", after="")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        model = session.capture()
        assert isinstance(model, ScreenModel)
        assert model.mode == "buffer"
        assert model.width == 80
        assert model.height == 24

    def test_buffer_has_cursor(self):
        proc = DummyProcess(before="abc\n", after="")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        model = session.capture()
        assert isinstance(model.cursor, CursorPosition)

    def test_last_preview_updated_on_capture(self, session):
        session.process.before = "hello world\n"
        session.capture()
        assert session._last_preview == "hello world"

    def test_last_preview_empty_on_no_output(self, session):
        session.process.before = ""
        session.capture()
        assert session._last_preview == ""


class TestClose:
    def test_closes_process(self, session):
        session.close()
        assert session.process._closed is True

    def test_double_close(self, session):
        session.close()
        session.close()  # Should not raise
        assert session.process._closed is True


class TestLiveness:
    """A dead child must fail loudly where a stale state would be believed."""

    def test_buffer_capture_raises_after_exit(self):
        proc = DummyProcess(alive=False)
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer", session_id="dead")
        with pytest.raises(ToolError, match="dead"):
            session.capture()

    def test_stream_capture_still_returns_recorded_output(self):
        """A finished CLI command's output is a record, not a stale live frame."""
        proc = DummyProcess(alive=False, before="git status output\n")
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        model = session.capture()
        assert "git status output" in model.text

    def test_send_raises_after_exit_in_both_modes(self):
        for mode in ("stream", "buffer"):
            proc = DummyProcess(alive=False)
            session = ScreenSession(process=proc, dimensions=(80, 24), mode=mode)
            with pytest.raises(ToolError):
                session.send("q")
            assert proc.sent == []

    def test_exit_status_is_reported(self):
        proc = DummyProcess(alive=False)
        proc.exitstatus = 3
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        with pytest.raises(ToolError, match="status 3"):
            session.assert_alive()

    def test_live_process_passes(self, session):
        session.assert_alive()  # must not raise

    def test_duck_typed_process_without_isalive_passes(self):
        class Bare:
            def send(self, keys):
                pass

        session = ScreenSession(process=Bare(), dimensions=(80, 24), mode="stream")
        session.assert_alive()  # no isalive attribute -> treated as alive


class TestResize:
    def test_resize_sets_winsize_and_screen(self):
        proc = DummyProcess()
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        session.resize(100, 30, delay=0)
        assert proc.winsize == (30, 100)
        assert session.dimensions == (100, 30)
        assert session.screen.columns == 100
        assert session.screen.lines == 30

    def test_resize_stream_sets_winsize_only(self):
        proc = DummyProcess()
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        session.resize(40, 12, delay=0)
        assert proc.winsize == (12, 40)
        assert session.dimensions == (40, 12)


class TestWideCharCapture:
    def test_capture_survives_wide_char_redraw(self):
        """pyte's display raises IndexError on this buffer; capture must not."""
        session = ScreenSession(process=DummyProcess(), dimensions=(12, 2), mode="buffer")
        session.stream.feed("\U0001F3F4")
        session.stream.feed("\x1b[1;1Hx")
        model = session.capture()
        assert model.lines[0].startswith("x")

    def test_capture_rows_are_column_exact(self):
        session = ScreenSession(process=DummyProcess(), dimensions=(10, 2), mode="buffer")
        session.stream.feed("a\U0001F3F4b")
        model = session.capture(preserve_format=True)
        assert model.lines[0] == "a\U0001F3F4 b      "
        assert len(model.lines[0]) == 10


class TestTerminalQueries:
    """A buffer-mode session must answer what the app asks the terminal.

    An app that queries cursor position (`ESC[6n`) blocks on the reply and, unanswered,
    exits with an I/O error. pyte computes the reply; the session owns the child's stdin.
    """

    def test_cursor_position_query_is_answered(self):
        proc = DummyProcess()
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        session.stream.feed("\x1b[3;5Hx")
        session.stream.feed("\x1b[6n")
        assert proc.sent == ["\x1b[3;6R"]

    def test_device_attributes_query_is_answered(self):
        proc = DummyProcess()
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        session.stream.feed("\x1b[c")
        assert proc.sent and proc.sent[0].endswith("c")

    def test_no_reply_without_a_query(self):
        proc = DummyProcess()
        session = ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")
        session.stream.feed("plain output")
        assert proc.sent == []

class TestSpecialKeysMapping:
    """Verify SPECIAL_KEYS dictionary integrity."""

    def test_all_special_keys_have_valid_sequences(self):
        from mcp_tui_test._server import SPECIAL_KEYS

        for name, seq in SPECIAL_KEYS.items():
            assert isinstance(name, str) and name
            assert isinstance(seq, str) and seq
            # Every sequence should start with \x1b (escape) unless it's a single control char
            if name not in ("enter", "tab", "backspace"):
                assert seq.startswith("\x1b"), f"{name}: {seq!r}"

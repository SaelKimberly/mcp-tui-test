"""Unit tests for ScreenSession (DummyProcess, no real subprocesses)."""

from mcp_tui_test._server import ScreenSession, ScreenModel, CursorPosition
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


class TestClose:
    def test_closes_process(self, session):
        session.close()
        assert session.process._closed is True

    def test_double_close(self, session):
        session.close()
        session.close()  # Should not raise
        assert session.process._closed is True

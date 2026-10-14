"""Shared fixtures for mcp-tui-test test suite."""

import pexpect
import pytest

from mcp_tui_test._server import ScreenSession, sessions


class DummyProcess:
    """Test double for pexpect.spawn — .send(), .expect(), .before, .after, etc.

    Avoids real subprocesses in unit tests. Mimics pexpect's expect/before/after
    lifecycle without the read_nonblocking ↔ expect data race.
    """

    def __init__(self, before: str = "", after: str = "", alive: bool = True):
        self.before = before
        self.after = after
        self.sent: list[str] = []
        self._closed = False
        self._timeout = 30
        self.alive = alive
        self.exitstatus: int | None = None
        self.signalstatus: int | None = None
        self.winsize: tuple[int, int] = (24, 80)

    def send(self, keys: str) -> None:
        self.sent.append(keys)

    def read_nonblocking(self, size: int = 4096, timeout: float = 0.1) -> str:
        if self._closed or not self.alive:
            raise pexpect.EOF("Process closed")
        return ""

    def isalive(self) -> bool:
        return self.alive and not self._closed

    def setwinsize(self, rows: int, cols: int) -> None:
        self.winsize = (rows, cols)

    def expect(self, patterns, timeout: int = -1) -> int:
        full = (self.before or "") + (self.after or "")
        for i, p in enumerate(patterns):
            if isinstance(p, str) and p in full:
                idx = full.index(p)
                self.before = full[:idx]
                self.after = full[idx : idx + len(p)]
                remaining = full[idx + len(p) :]
                if remaining:
                    self.before += remaining
                return i
        raise pexpect.TIMEOUT("Pattern not found")

    @property
    def timeout(self) -> int:
        return self._timeout

    @timeout.setter
    def timeout(self, value: int) -> None:
        self._timeout = value

    def close(self) -> None:
        self._closed = True


@pytest.fixture
def dummy_process():
    """A DummyProcess with some initial output."""
    return DummyProcess(before="header\nmenu line 1\nmenu line 2\n", after="")


@pytest.fixture
def session(dummy_process):
    """A ScreenSession in stream mode with the dummy process."""
    return ScreenSession(process=dummy_process, dimensions=(80, 24), mode="stream")


@pytest.fixture
def buffer_session():
    """A ScreenSession in buffer mode with a dummy process."""
    proc = DummyProcess(before="header\nmenu line 1\n", after="")
    return ScreenSession(process=proc, dimensions=(80, 24), mode="buffer")


@pytest.fixture
def fresh_sessions():
    """Provide a clean sessions dict for require_session tests.

    Saves and restores the global dict to avoid cross-test pollution.
    """
    original = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(original)

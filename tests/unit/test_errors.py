"""Tests for require_session helper and ToolError behavior."""

import pytest
from fastmcp.exceptions import ToolError

from mcp_tui_test._server import ScreenSession, require_session


class TestRequireSession:
    def test_found(self, fresh_sessions):
        """require_session returns the session when it exists."""
        from mcp_tui_test._server import sessions
        from tests.conftest import DummyProcess

        proc = DummyProcess()
        s = ScreenSession(process=proc, dimensions=(80, 24), mode="stream")
        sessions["test_sess"] = s
        result = require_session("test_sess")
        assert result is s

    def test_not_found(self, fresh_sessions):
        """require_session raises ToolError when session doesn't exist."""
        with pytest.raises(ToolError, match="No active session found"):
            require_session("nonexistent")

    def test_error_mentions_session_id(self, fresh_sessions):
        """The error message includes the requested session_id."""
        with pytest.raises(ToolError) as exc:
            require_session("missing-session")
        assert "missing-session" in str(exc.value)

    def test_tool_error_importable(self):
        """ToolError is importable from fastmcp.exceptions."""
        from fastmcp.exceptions import ToolError as TE
        assert TE is ToolError

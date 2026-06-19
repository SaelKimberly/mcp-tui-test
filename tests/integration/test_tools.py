"""Integration tests for all 12 MCP tools via mcp.call_tool().

Each test launches the example TUI app in a real subprocess.
Marked @pytest.mark.integration to allow exclusion from fast runs.
"""

import pytest
from fastmcp.exceptions import ToolError
from mcp_tui_test._server import mcp

EXAMPLE_APP = "python examples/example_tui_app.py"


@pytest.fixture
async def launched_session():
    """Launch the example TUI app (stream mode). Cleanup on teardown."""
    r = await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP})
    yield r
    try:
        await mcp.call_tool("close_session", {})
    except Exception:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
class TestLaunchAndLifecycle:
    async def test_launch_tui(self):
        r = await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP})
        assert "✓" in str(r)
        await mcp.call_tool("close_session", {})

    async def test_double_launch_replaces(self):
        r1 = await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP})
        assert "✓" in str(r1)
        r2 = await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP})
        assert "✓" in str(r2)
        await mcp.call_tool("close_session", {})

    async def test_close_session(self, launched_session):
        r = await mcp.call_tool("close_session", {})
        assert "✓" in str(r)

    async def test_list_sessions(self, launched_session):
        r = await mcp.call_tool("list_sessions", {})
        assert "default" in str(r)

    async def test_bad_session_id_raises_toolerror(self):
        with pytest.raises(ToolError):
            await mcp.call_tool("send_keys", {"keys": "x", "session_id": "nonexistent"})

    async def test_operate_after_close_raises_toolerror(self, launched_session):
        await mcp.call_tool("close_session", {})
        with pytest.raises(ToolError):
            await mcp.call_tool("send_keys", {"keys": "x"})


@pytest.mark.integration
@pytest.mark.asyncio
class TestExpectText:
    async def test_found(self, launched_session):
        r = await mcp.call_tool("expect_text", {"pattern": "Welcome"})
        assert "✓" in str(r)

    async def test_timeout(self, launched_session):
        with pytest.raises(ToolError, match="Timeout"):
            await mcp.call_tool("expect_text", {"pattern": "ZZZZ_NOT_THERE", "timeout": 1})


@pytest.mark.integration
@pytest.mark.asyncio
class TestSendAndCapture:
    async def test_send_keys_then_expect(self, launched_session):
        await mcp.call_tool("expect_text", {"pattern": "Welcome"})
        r = await mcp.call_tool("send_keys", {"keys": "1\n"})
        assert "✓" in str(r)
        r = await mcp.call_tool("expect_text", {"pattern": "Hello, TUI Tester!"})
        assert "✓" in str(r)

    async def test_capture_screen(self, launched_session):
        await mcp.call_tool("expect_text", {"pattern": "Welcome"})
        r = await mcp.call_tool("capture_screen", {})
        assert "Welcome" in str(r)

@pytest.mark.integration
@pytest.mark.asyncio
class TestAssertions:
    async def test_assert_contains_pass(self, launched_session):
        await mcp.call_tool("expect_text", {"pattern": "Welcome"})
        r = await mcp.call_tool("assert_contains", {"text": "Welcome"})
        assert "✓" in str(r)

    async def test_assert_contains_fail(self, launched_session):
        await mcp.call_tool("expect_text", {"pattern": "Welcome"})
        with pytest.raises(ToolError, match="Assertion failed"):
            await mcp.call_tool("assert_contains", {"text": "NOTTHERE"})
@pytest.mark.integration

@pytest.mark.asyncio
class TestBufferMode:
    @pytest.fixture
    async def buffer_launched(self):
        """Launch in buffer mode and wait for Welcome."""
        await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP, "mode": "buffer"})
        try:
            await mcp.call_tool("expect_text", {"pattern": "Welcome"})
            yield
        finally:
            try:
                await mcp.call_tool("close_session", {})
            except Exception:
                pass

    async def test_assert_at_position(self, buffer_launched):
        r = await mcp.call_tool("assert_at_position", {"text": "Welcome", "row": 1, "col": 2})
        assert "✓" in str(r)

    async def test_get_cursor_position(self, buffer_launched):
        r = await mcp.call_tool("get_cursor_position", {})
        assert "row" in str(r)

    async def test_get_line(self, buffer_launched):
        r = await mcp.call_tool("get_line", {"row": 1})
        assert "Welcome" in str(r)

    async def test_get_screen_region(self, buffer_launched):
        r = await mcp.call_tool("get_screen_region", {"row_start": 0, "row_end": 5})
        assert "Welcome" in str(r)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSendCtrl:
    async def test_send_ctrl(self, launched_session):
        r = await mcp.call_tool("send_ctrl", {"key": "c"})
        assert "✓" in str(r)

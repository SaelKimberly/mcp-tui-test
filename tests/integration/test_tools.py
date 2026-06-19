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
        with pytest.raises(ToolError, match="not found after"):
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

    async def test_expect_text_regex_pattern(self, buffer_launched):
        r = await mcp.call_tool("expect_text", {"pattern": r"Welcome.*Example"})
        assert "✓" in str(r)

    async def test_get_char(self, buffer_launched):
        r = await mcp.call_tool("get_char", {"row": 1, "col": 2})
        assert "Character at" in str(r)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSendCtrl:
    async def test_send_ctrl(self, launched_session):
        r = await mcp.call_tool("send_ctrl", {"key": "c"})
        assert "✓" in str(r)


@pytest.mark.integration
@pytest.mark.asyncio
class TestNewTools:
    """Integration tests for new tools (wait_for_stable, wait_for_change, get_session_info, etc.)"""

    @pytest.fixture
    async def launched(self):
        await mcp.call_tool(
            "launch_tui", {"command": EXAMPLE_APP, "mode": "buffer"},
        )
        try:
            await mcp.call_tool("expect_text", {"pattern": "Welcome"})
            yield
        finally:
            try:
                await mcp.call_tool("close_session", {})
            except Exception:
                pass

    async def test_wait_for_stable(self, launched):
        r = await mcp.call_tool(
            "wait_for_stable", {"interval": 0.05, "consecutive": 2, "timeout": 5},
        )
        assert "Screen stabilized" in str(r)
        assert "Welcome" in str(r)

    async def test_wait_for_change(self, launched):
        r = await mcp.call_tool("capture_screen", {})
        ref = str(r)
        await mcp.call_tool("send_keys", {"keys": "1\n"})
        r = await mcp.call_tool(
            "wait_for_change", {"reference": ref, "timeout": 5},
        )
        assert "Screen changed" in str(r)
        assert "Hello, TUI Tester!" in str(r)

    async def test_get_session_info(self, launched):
        r = await mcp.call_tool("get_session_info", {})
        text = str(r)
        assert "Session: default" in text
        assert "Mode:" in text
        assert "PID:" in text
        assert "Uptime:" in text

    async def test_send_keys_inter_key_delay(self, launched):
        r = await mcp.call_tool(
            "send_keys", {"keys": "1\n", "inter_key_delay": 0.05},
        )
        assert "✓" in str(r)

    async def test_auto_session_id(self):
        r = await mcp.call_tool(
            "launch_tui", {"command": EXAMPLE_APP, "session_id": ""},
        )
        text = str(r)
        assert "session_" in text
        # Extract auto-generated session ID from response
        import re

        match = re.search(r"session: (session_\d+)", text)
        assert match, f"Could not find session ID in: {text}"
        auto_id = match.group(1)
        await mcp.call_tool("close_session", {"session_id": auto_id})


@pytest.mark.integration
@pytest.mark.asyncio
class TestRatatuiApp:
    """Integration tests using a real ratatui binary (crossterm + alternate screen).

    Verifies that pyte tracks ratatui's alternate-screen ANSI output,
    that Tab key switches tabs, and that Enter (\\r) works for crossterm apps.
    """

    RATATUI_APP = "/tmp/ratatui-test-app/target/release/ratatui-test-app"

    @pytest.fixture
    async def ratatui_session(self):
        await mcp.call_tool("launch_tui", {
            "command": self.RATATUI_APP,
            "mode": "buffer",
            "dimensions": "120x40",
            "timeout": 15,
        })
        try:
            await mcp.call_tool("expect_text", {"pattern": "Main", "timeout": 5})
            yield
        finally:
            try:
                await mcp.call_tool("close_session", {})
            except Exception:
                pass

    async def test_launch_shows_content(self, ratatui_session):
        r = await mcp.call_tool("capture_screen", {})
        text = str(r)
        assert "Main Tab" in text
        assert "Counter: 0" in text
        assert "Press 'q' to quit" in text

    async def test_tab_switch(self, ratatui_session):
        await mcp.call_tool("send_keys", {"keys": "\t", "delay": 0.3})
        r = await mcp.call_tool("expect_text", {"pattern": "Settings", "timeout": 5})
        assert "✓" in str(r)

    async def test_enter_increments_counter(self, ratatui_session):
        """Crossterm apps expect \\r for Enter, not \\n."""
        await mcp.call_tool("send_keys", {"keys": "a\r", "delay": 0.3})
        r = await mcp.call_tool("capture_screen", {})
        text = str(r)
        assert "Counter: 2" in text  # 'a' + Enter = 2 increments

    async def test_send_q_quits(self, ratatui_session):
        await mcp.call_tool("send_keys", {"keys": "q", "delay": 0.5})
        # Process exited; session should still be usable for capture
        await mcp.call_tool("capture_screen", {})
        # Should not raise

    async def test_get_line_returns_content(self, ratatui_session):
        r = await mcp.call_tool("get_line", {"row": 3})
        assert "Main Tab" in str(r) or "Main" in str(r)

    async def test_get_cursor_position(self, ratatui_session):
        r = await mcp.call_tool("get_cursor_position", {})
        text = str(r)
        assert "row" in text
        assert "column" in text

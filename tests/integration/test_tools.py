import asyncio

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
class TestSendSpecialKeys:
    """Integration tests for send_special_keys tool."""

    async def test_send_special_keys_basic(self, launched_session):
        r = await mcp.call_tool("send_special_keys", {"keys": ["escape"]})
        assert "✓" in str(r)

    async def test_send_special_keys_buffer_mode(self):
        await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP, "mode": "buffer"})
        try:
            await mcp.call_tool("expect_text", {"pattern": "Welcome"})
            r = await mcp.call_tool("send_special_keys", {"keys": ["down", "enter"]})
            assert "✓" in str(r)
        finally:
            try:
                await mcp.call_tool("close_session", {})
            except Exception:
                pass

    async def test_send_special_keys_unknown_key(self, launched_session):
        with pytest.raises(ToolError) as exc:
            await mcp.call_tool("send_special_keys", {"keys": ["notakey"]})
        assert "Unknown special key" in str(exc.value)


@pytest.mark.integration
@pytest.mark.asyncio
class TestNewTools:
    """Integration tests for new tools (wait_for_stable, wait_for_change, get_session_info, etc.)"""

    @pytest.fixture
    async def launched(self):
        await mcp.call_tool(
            "launch_tui",
            {"command": EXAMPLE_APP, "mode": "buffer"},
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
            "wait_for_stable",
            {"interval": 0.05, "consecutive": 2, "timeout": 5},
        )
        assert "Screen stabilized" in str(r)
        assert "Welcome" in str(r)

    async def test_wait_for_change(self, launched):
        r = await mcp.call_tool("capture_screen", {})
        ref = str(r)
        await mcp.call_tool("send_keys", {"keys": "1\n"})
        r = await mcp.call_tool(
            "wait_for_change",
            {"reference": ref, "timeout": 5},
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
            "send_keys",
            {"keys": "1\n", "inter_key_delay": 0.05},
        )
        assert "✓" in str(r)

    async def test_auto_session_id(self):
        r = await mcp.call_tool(
            "launch_tui",
            {"command": EXAMPLE_APP, "session_id": ""},
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
        await mcp.call_tool(
            "launch_tui",
            {
                "command": self.RATATUI_APP,
                "mode": "buffer",
                "dimensions": "120x40",
                "timeout": 15,
            },
        )
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


@pytest.fixture
def scenario_sessions():
    """Clean session table for run_scenario tests; close whatever the test left behind."""
    from mcp_tui_test._server import sessions as live_sessions

    original = dict(live_sessions)
    live_sessions.clear()
    yield live_sessions
    for session in list(live_sessions.values()):
        try:
            session.close()
        except Exception:
            pass
    live_sessions.clear()
    live_sessions.update(original)


EXAMPLE_SCENARIO = """
launch("python examples/example_tui_app.py", mode="stream", dimensions="100x30", timeout=20)
expect("Please choose an option", timeout=10)
send("3\\n", delay=0.5)
# Stream mode only advances its accumulator on expect(), so read through one before asserting.
expect("Counter value: 1", timeout=10)
assert_text("Counter value: 1")
close()
"""


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunScenario:
    async def test_executes_pipeline_and_closes_the_session(self, scenario_sessions):
        r = await mcp.call_tool("run_scenario", {"ad_hoc": EXAMPLE_SCENARIO})
        payload = r.structured_content
        assert payload["ok"] is True
        assert payload["action"] == "executed"
        assert [result["status"] for result in payload["results"]] == ["ok"] * 6
        assert [step["tool"] for step in payload["pipeline"]] == [
            "launch_tui",
            "expect_text",
            "send_keys",
            "expect_text",
            "assert_contains",
            "close_session",
        ]
        assert payload["session_alive"] is False
        assert "No active sessions" in (await mcp.call_tool("list_sessions", {})).content[0].text

    async def test_lint_errors_never_launch_anything(self, scenario_sessions):
        r = await mcp.call_tool("run_scenario", {"ad_hoc": 'launch("python examples/example_tui_app.py")\nsend_keyz("q")'})
        payload = r.structured_content
        assert payload["ok"] is False
        assert payload["action"] == "linted"
        assert payload["results"] == []
        assert [step["tool"] for step in payload["pipeline"]] == ["launch_tui"]  # what *would* run
        assert "E_UNKNOWN_STEP" in {d["code"] for d in payload["diagnostics"]}
        assert "No active sessions" in (await mcp.call_tool("list_sessions", {})).content[0].text

    async def test_dry_run_resolves_the_pipeline_without_touching_a_session(self, scenario_sessions):
        r = await mcp.call_tool("run_scenario", {"ad_hoc": EXAMPLE_SCENARIO, "dry_run": True})
        payload = r.structured_content
        assert payload["ok"] is True and payload["action"] == "dry_run"
        assert len(payload["pipeline"]) == 6
        assert payload["results"] == [] and payload["session_alive"] is False
        assert "No active sessions" in (await mcp.call_tool("list_sessions", {})).content[0].text

    async def test_attaches_to_a_live_session_and_keeps_it_alive(self, scenario_sessions):
        await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP, "session_id": "attached", "mode": "stream"})
        await mcp.call_tool("expect_text", {"pattern": "Please choose an option", "session_id": "attached", "timeout": 10})
        script = """
send("3\\n", delay=0.5)
expect("Counter value: 1", timeout=10)
assert_text("Counter value: 1")
capture()
"""
        r = await mcp.call_tool("run_scenario", {"ad_hoc": script, "session_id": "attached"})
        payload = r.structured_content
        assert payload["attached"] is True
        assert payload["kept_alive"] is True and payload["session_alive"] is True
        assert "attached" in r.content[0].text
        # The session survives, so a follow-up capture still works.
        capture = await mcp.call_tool("capture_screen", {"session_id": "attached"})
        assert "Counter value: 1" in capture.content[0].text
        await mcp.call_tool("close_session", {"session_id": "attached"})

    async def test_keep_alive_false_closes_a_launched_session(self, scenario_sessions):
        script = 'launch("python examples/example_tui_app.py", mode="stream")\nexpect("Please choose an option", timeout=10)\n'
        r = await mcp.call_tool("run_scenario", {"ad_hoc": script, "keep_alive": False})
        payload = r.structured_content
        assert payload["session_alive"] is False and payload["kept_alive"] is False
        assert "No active sessions" in (await mcp.call_tool("list_sessions", {})).content[0].text

    async def test_missing_session_without_a_launch_step_is_reported(self, scenario_sessions):
        r = await mcp.call_tool("run_scenario", {"ad_hoc": 'expect("anything")'})
        payload = r.structured_content
        assert payload["ok"] is False
        assert "E_NO_SESSION" in {d["code"] for d in payload["diagnostics"]}
        assert payload["results"] == []

    async def test_stop_on_error_default_and_continue(self, scenario_sessions):
        script = """
launch("python examples/example_tui_app.py", mode="stream")
assert_text("NOT_ON_THE_SCREEN")
capture()
"""
        stopped = (await mcp.call_tool("run_scenario", {"ad_hoc": script})).structured_content
        assert stopped["ok"] is False
        assert [result["status"] for result in stopped["results"]] == ["ok", "error"]

        continued = (await mcp.call_tool("run_scenario", {"ad_hoc": script, "stop_on_error": False})).structured_content
        assert [result["status"] for result in continued["results"]] == ["ok", "error", "ok"]

    async def test_launch_env_and_pinned_locale_reach_the_child(self, scenario_sessions):
        script = """
launch("sh -c 'echo MARKER=$OMP_TUI_TEST_MARKER LOCALE=$LC_ALL'", mode="stream", env={"OMP_TUI_TEST_MARKER": "yes"})
expect("MARKER=yes LOCALE=C", timeout=10)
close()
"""
        payload = (await mcp.call_tool("run_scenario", {"ad_hoc": script})).structured_content
        assert payload["ok"] is True, payload["results"]

    async def test_pin_env_false_leaves_the_inherited_locale_alone(self, scenario_sessions):
        script = """
launch("sh -c 'echo MARKER=$OMP_TUI_TEST_MARKER'", mode="stream", env={"OMP_TUI_TEST_MARKER": "yes"})
expect("MARKER=yes", timeout=10)
close()
"""
        payload = (await mcp.call_tool("run_scenario", {"ad_hoc": script, "pin_env": False})).structured_content
        assert payload["ok"] is True, payload["results"]

    async def test_launch_tui_env_parameter(self, scenario_sessions):
        await mcp.call_tool(
            "launch_tui",
            {
                "command": "sh -c 'echo MARKER=$OMP_TUI_TEST_MARKER'",
                "session_id": "envcheck",
                "mode": "stream",
                "env": {"OMP_TUI_TEST_MARKER": "yes"},
            },
        )
        r = await mcp.call_tool("expect_text", {"pattern": "MARKER=yes", "session_id": "envcheck", "timeout": 10})
        assert "✓" in str(r)
        await mcp.call_tool("close_session", {"session_id": "envcheck"})

    async def test_rescue_files_are_reported(self, scenario_sessions, tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("Line one\n")
        script = f"""
launch("env TERM=xterm-256color LC_ALL=C nano {notes}", mode="buffer", dimensions="120x40", timeout=20)
expect("GNU nano", timeout=10)
send("modified and abandoned", delay=0.5)
close()
"""
        payload = (await mcp.call_tool("run_scenario", {"ad_hoc": script})).structured_content
        assert payload["ok"] is True, payload["results"]
        assert payload["rescue_files"] == [str(tmp_path / "notes.txt.save")]


    async def test_failed_launch_does_not_kill_an_attached_session(self, scenario_sessions):
        """A launch step that raises must not close a session this call did not create."""
        await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP, "session_id": "victim", "mode": "stream"})
        await mcp.call_tool("expect_text", {"pattern": "Please choose an option", "session_id": "victim", "timeout": 10})
        payload = (
            await mcp.call_tool(
                "run_scenario",
                {
                    "ad_hoc": 'launch("python examples/example_tui_app.py", dimensions="not-a-size")\n',
                    "session_id": "victim",
                    "keep_alive": False,
                },
            )
        ).structured_content
        assert payload["ok"] is False
        assert payload["session_alive"] is True, payload
        assert "victim" in scenario_sessions
        assert "Please choose an option" in (
            await mcp.call_tool("capture_screen", {"session_id": "victim"})
        ).content[0].text
        await mcp.call_tool("close_session", {"session_id": "victim"})

    async def test_explicit_keep_alive_false_closes_an_attached_session(self, scenario_sessions):
        await mcp.call_tool("launch_tui", {"command": EXAMPLE_APP, "session_id": "attached2", "mode": "stream"})
        await mcp.call_tool("expect_text", {"pattern": "Please choose an option", "session_id": "attached2", "timeout": 10})
        payload = (
            await mcp.call_tool("run_scenario", {"ad_hoc": "capture()\n", "session_id": "attached2", "keep_alive": False})
        ).structured_content
        assert payload["ok"] is True
        assert payload["kept_alive"] is False and payload["session_alive"] is False
        assert "attached2" not in scenario_sessions

    async def test_concurrent_runs_on_one_session_id_do_not_raise(self, scenario_sessions):
        script = 'launch("python examples/example_tui_app.py", mode="stream")\nexpect("Please choose an option", timeout=10)\n'
        results = await asyncio.gather(
            mcp.call_tool("run_scenario", {"ad_hoc": script, "session_id": "shared"}),
            mcp.call_tool("run_scenario", {"ad_hoc": script, "session_id": "shared"}),
            return_exceptions=True,
        )
        assert all(not isinstance(result, BaseException) for result in results), results
        for session in list(scenario_sessions.values()):
            session.close()

    async def test_empty_session_id_is_rejected(self, scenario_sessions):
        payload = (
            await mcp.call_tool("run_scenario", {"ad_hoc": 'launch("python examples/example_tui_app.py")\n', "session_id": ""})
        ).structured_content
        assert payload["ok"] is False
        assert "E_BAD_SESSION_ID" in {d["code"] for d in payload["diagnostics"]}
        assert scenario_sessions == {}

    async def test_out_of_range_dimensions_fail_fast_without_launching(self, scenario_sessions):
        payload = (
            await mcp.call_tool("run_scenario", {"ad_hoc": 'launch("cat", dimensions="200000x200000", mode="stream")\n'})
        ).structured_content
        assert payload["ok"] is False
        assert payload["action"] == "linted"
        assert "E_ARG_VALUE" in {d["code"] for d in payload["diagnostics"]}
        assert scenario_sessions == {}

    async def test_launch_tui_rejects_out_of_range_dimensions(self, scenario_sessions):
        with pytest.raises(ToolError):
            await mcp.call_tool("launch_tui", {"command": "cat", "mode": "stream", "dimensions": "200000x200000"})
        assert scenario_sessions == {}


    CHOICE_DOC = """---
name: choice-epilogue
description: Reports which of several possible end states the app reached.
app: example app
---

# Choice epilogue

```python
launch("python examples/example_tui_app.py", mode="buffer", dimensions="100x30", timeout=20)
expect("Please choose an option", timeout=10)
choice({
    "menu visible": expect("Please choose an option", timeout=3),
    "quit hint visible": assert_text("q. Quit"),
    "counter visible": assert_text("Counter value: 1"),
})
```
"""

    async def test_document_mode_runs_a_choice_epilogue(self, scenario_sessions):
        run = await mcp.call_tool("run_scenario", {"script": self.CHOICE_DOC})
        payload = run.structured_content
        assert payload["ok"] is True, payload["results"]
        assert payload["header"]["name"] == "choice-epilogue"
        assert payload["choice"]["matched"] == ["menu visible", "quit hint visible"]
        assert [record["status"] for record in payload["choice"]["evaluated"]] == ["matched", "matched", "no match"]
        assert "CHOICE → matched: 'menu visible', 'quit hint visible'" in run.content[0].text
        assert payload["session_alive"] is False

    async def test_choice_without_a_match_fails_the_step(self, scenario_sessions):
        doc = self.CHOICE_DOC.replace('"menu visible": expect("Please choose an option", timeout=3)', '"menu visible": expect("never shown", timeout=1)').replace(
            '"quit hint visible": assert_text("q. Quit"),', ""
        )
        payload = (await mcp.call_tool("run_scenario", {"script": doc})).structured_content
        assert payload["ok"] is False
        assert payload["choice"]["matched"] == []
        assert payload["results"][-1]["step"] == "choice" and payload["results"][-1]["status"] == "error"
        assert "no alternative matched" in payload["results"][-1]["output"]

    async def test_ad_hoc_mode_accepts_raw_source(self, scenario_sessions):
        script = 'launch("python examples/example_tui_app.py", mode="stream")\nexpect("Please choose an option", timeout=10)\nclose()\n'
        payload = (await mcp.call_tool("run_scenario", {"ad_hoc": script})).structured_content
        assert payload["ok"] is True
        assert payload["header"] == {}
        assert [result["status"] for result in payload["results"]] == ["ok", "ok", "ok"]

    async def test_input_mode_must_be_exactly_one(self, scenario_sessions):
        for kwargs in ({}, {"script": self.CHOICE_DOC, "ad_hoc": "close()"}):
            payload = (await mcp.call_tool("run_scenario", kwargs)).structured_content
            assert payload["ok"] is False
            assert [d["code"] for d in payload["diagnostics"]] == ["E_INPUT_MODE"]
            assert scenario_sessions == {}

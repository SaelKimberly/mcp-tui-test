#!/usr/bin/env python3
"""Probe script: diagnose send_keys / expect_text behavior with ratatui apps."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_tui_test._server import mcp, sessions

RATATUI_APP = "/tmp/ratatui-test-app/target/release/ratatui-test-app"


def text(r):
    """Extract text from a ToolResult."""
    parts = []
    for item in r.content if hasattr(r, "content") else [r]:
        if hasattr(item, "text"):
            parts.append(item.text)
        else:
            parts.append(str(item))
    return "\n".join(parts)


async def tool(name, **kwargs):
    """Call an MCP tool and return the text result."""
    r = await mcp.call_tool(name, kwargs)
    return text(r)


async def run_test(name, coro):
    print(f"\n{'=' * 60}")
    print(f"  [{name}]")
    print(f"{'=' * 60}")
    try:
        result = await coro
        print(f"  ✓ {result}")
        return True
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        return False


async def cleanup():
    for sid in list(sessions.keys()):
        try:
            await tool("close_session", session_id=sid)
        except Exception:
            pass


async def step1_baseline():
    """Step 1.1: Basic launch and initial screen capture."""
    await cleanup()
    r = await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print(f"  Launch: {r}")
    await asyncio.sleep(1.5)

    screen = await tool("capture_screen", session_id="probe")
    print(f"  Screen capture:\n{screen}")

    for i in range(5):
        try:
            line = await tool("get_line", row=i, session_id="probe")
            print(f"  Line {i}: {line}")
        except Exception as e:
            print(f"  Line {i}: error - {e}")

    for label in ["Main", "Tabs", "Counter"]:
        ok = await run_test(
            f"assert_contains('{label}')",
            tool("assert_contains", text=label, session_id="probe"),
        )
        if not ok:
            print(f"  → '{label}' NOT found on screen")


async def step2_send_q():
    """Step 1.2: Send 'q' and verify process exits."""
    await tool("send_keys", keys="q", session_id="probe", delay=0.5)
    print("  Sent 'q'")
    await asyncio.sleep(0.5)
    try:
        screen = await tool("capture_screen", session_id="probe")
        print(f"  Screen after 'q':\n{screen}")
    except Exception as e:
        print(f"  capture_screen: {e}")
    await cleanup()


async def step3_tab_switch():
    """Step 1.3: Send Tab to switch tabs, then expect_text."""
    await cleanup()
    await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print("  Launch OK")
    await asyncio.sleep(1.5)

    ok = await run_test(
        "expect_text Main",
        tool("expect_text", pattern="Main", session_id="probe", timeout=5),
    )
    if not ok:
        print("  → Cannot proceed: initial text not found")
        await cleanup()
        return

    # Send Tab using \t (codecs.decode will convert to actual tab)
    await tool("send_keys", keys="\t", session_id="probe", delay=0.5)
    print("  Sent Tab (\\t)")

    ok = await run_test(
        "expect_text Settings after Tab",
        tool("expect_text", pattern="Settings", session_id="probe", timeout=5),
    )

    if not ok:
        # Try \\x09 literal escape
        print("  → \\t failed, trying \\x09")
        await tool("send_keys", keys="\\x09", session_id="probe", delay=0.5)
        ok = await run_test(
            "expect_text Settings after \\x09",
            tool("expect_text", pattern="Settings", session_id="probe", timeout=5),
        )

    screen = await tool("capture_screen", session_id="probe")
    print(f"  Screen:\n{screen}")
    await cleanup()


async def step4_enter_key():
    """Step 1.4: Test Enter key (\\n vs \\r)."""
    await cleanup()
    await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print("  Launch OK")
    await asyncio.sleep(1.5)

    # Test with \r (CR - what crossterm expects)
    await tool("send_keys", keys="a\r", session_id="probe", delay=0.5)
    print("  Sent 'a\\r'")
    await asyncio.sleep(0.5)

    screen = await tool("capture_screen", session_id="probe")
    print(f"  Screen after 'a\\r':\n{screen}")

    ok = await run_test(
        "Counter: 1 after 'a\\r'",
        tool("assert_contains", text="Counter: 1", session_id="probe"),
    )

    # Send q to quit
    await tool("send_keys", keys="q", session_id="probe", delay=0.5)
    await cleanup()

    # Now test with \n
    await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print("  Launch OK (for \\n test)")
    await asyncio.sleep(1.5)

    await tool("send_keys", keys="a\n", session_id="probe", delay=0.5)
    print("  Sent 'a\\n'")
    await asyncio.sleep(0.5)

    screen = await tool("capture_screen", session_id="probe")
    print(f"  Screen after 'a\\n':\n{screen}")

    ok = await run_test(
        "Counter: 1 after 'a\\n'",
        tool("assert_contains", text="Counter: 1", session_id="probe"),
    )
    if not ok:
        print("  → \\n did NOT increment counter (crossterm expects \\r)")

    await tool("send_keys", keys="q", session_id="probe", delay=0.5)
    await cleanup()


async def step5_polling():
    """Step 1.5: Test expect_text buffer-mode polling with short delay."""
    await cleanup()
    await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print("  Launch OK")
    await asyncio.sleep(1.5)

    await tool("send_keys", keys="a\r", session_id="probe", delay=0.1)
    print("  Sent 'a\\r' with delay=0.1")

    await run_test(
        "Counter: 1 after 'a\\r'",
        tool("expect_text", pattern="Counter: 1", session_id="probe", timeout=5),
    )

    screen = await tool("capture_screen", session_id="probe")
    print(f"  Screen:\n{screen}")

    await tool("send_keys", keys="q", session_id="probe", delay=0.5)
    await cleanup()


async def step6_pyte_tracking():
    """Step 1.6: Determine if pyte tracks the screen correctly."""
    await cleanup()
    await tool(
        "launch_tui",
        command=RATATUI_APP,
        session_id="probe",
        timeout=15,
        dimensions="120x40",
        mode="buffer",
    )
    print("  Launch OK")
    await asyncio.sleep(1.5)

    raw_screen = await tool("capture_screen", session_id="probe", preserve_format=True)
    print(f"  Raw screen:\n{raw_screen}")

    print("  Non-empty lines:")
    for i in range(40):
        try:
            line_text = await tool("get_line", row=i, session_id="probe")
            # Format: "Line N: <content>"
            if ":" in line_text:
                content = line_text.split(":", 1)[1]
                if content.strip():
                    print(f"    {i:2d}: {content}")
        except Exception:
            break

    # Send 'a\r' and check tracking
    await tool("send_keys", keys="a\r", session_id="probe", delay=0.5)

    print("  After 'a\\r':")
    for i in range(40):
        try:
            line_text = await tool("get_line", row=i, session_id="probe")
            if ":" in line_text:
                content = line_text.split(":", 1)[1]
                if content.strip():
                    print(f"    {i:2d}: {content}")
        except Exception:
            break

    cursor = await tool("get_cursor_position", session_id="probe")
    print(f"  {cursor}")

    region = await tool(
        "get_screen_region",
        session_id="probe",
        row_start=0,
        row_end=10,
        col_start=0,
        col_end=80,
    )
    print(f"  Region 0-10×0-80:\n{region}")

    await tool("send_keys", keys="q", session_id="probe", delay=0.5)
    await cleanup()


async def main():
    print("=" * 60)
    print("  PROBE: Ratatui TUI Test Diagnosis")
    print("=" * 60)

    steps = [
        ("1.1 Launch & Capture", step1_baseline()),
        ("1.2 Send Key & Exit", step2_send_q()),
        ("1.3 Tab Switch", step3_tab_switch()),
        ("1.4 Enter Key (\\r vs \\n)", step4_enter_key()),
        ("1.5 Expect Text Polling", step5_polling()),
        ("1.6 Pyte Tracking", step6_pyte_tracking()),
    ]

    results = {}
    for name, coro in steps:
        try:
            await coro
            results[name] = True
        except Exception as e:
            print(f"\n  ✗ STEP FAILED: {name}: {e}")
            results[name] = False

    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")

    await cleanup()


if __name__ == "__main__":
    asyncio.run(main())

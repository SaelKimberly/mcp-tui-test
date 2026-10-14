# Mounting tui-test in omp, and running scenarios without a mount

## What the mount gives you

The MCP server is registered as `tui-test`; omp bridges each tool into
`mcp__<server>_<tool>` with the name lowercased and non-alphanumerics collapsed to `_`. So
`launch_tui` becomes:

```
mcp__tui_test_launch_tui
mcp__tui_test_send_keys
mcp__tui_test_capture_screen
mcp__tui_test_expect_text
mcp__tui_test_get_line
mcp__tui_test_get_screen_region
mcp__tui_test_assert_at_position
mcp__tui_test_wait_for_stable
mcp__tui_test_close_session
… one tool per server tool, 18 in total (the 17 primitives plus `run_scenario`)
```

Same arguments as `references/tools.md`; the mounted tool is the same function, so scenarios written
for one path replay on the other.

## Project-scope mount (checked in with the TUI project)

`<project>/.omp/mcp.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json",
  "mcpServers": {
    "tui-test": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--quiet", "mcp-tui-test"]
    }
  }
}
```

This is the shape shipped in the mcp-tui-test repository itself: `uv` finds the project by walking up
from the working directory, and `mcp-tui-test` is the console script (`mcp_tui_test:mcp.run`). It
requires [uv](https://docs.astral.sh/uv/) and the project's Python (≥3.14) — `uv run` provisions it.

## User-scope mount (available in every project)

Append to `~/.omp/agent/mcp.json`, pointing `--project` at an absolute checkout path so the server
does not depend on the current directory:

```json
{
  "mcpServers": {
    "tui-test": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--quiet", "--project", "/abs/path/to/mcp-tui-test", "mcp-tui-test"]
    }
  }
}
```

Editing that file changes user-level configuration, so propose the entry (or let the user run `/mcp add`)
rather than writing it as a side effect of a walkthrough.

## Verify the mount

- `omp` → `/mcp list` shows `tui-test` (`/mcp test tui-test` for a live handshake).
- `/mcp reload` after editing config in a running session.
- Sanity call: `mcp__tui_test_launch_tui` with
  `command="env TERM=xterm-256color LC_ALL=C <app>"`, then `mcp__tui_test_capture_screen`.
- Without omp, handshake the config directly (this is how the shipped `.omp/mcp.json` was verified:
  17 tools listed, launch/close round-trip):

```bash
uv run --project /abs/path/to/mcp-tui-test python - <<'PY'
import asyncio, json, pathlib
from fastmcp import Client

config = json.loads(pathlib.Path(".omp/mcp.json").read_text())

async def main():
    async with Client(config) as client:
        print(sorted(t.name for t in await client.list_tools()))

asyncio.run(main())
PY
```

Slow servers register after the first turn; if the `mcp__tui_test_*` tools are missing, the server did
not connect — drive `run_scenario` in-process instead of guessing tool names.

## Installing this skill itself

The skill is a directory (`SKILL.md` + `references/`). oh-my-pi discovers user skills one
level below `~/.omp/agent/skills/`, so either:

```bash
# Track the checkout (recommended while the skill is being iterated on)
ln -sfn /abs/path/to/mcp-tui-test/skills/tui-test ~/.omp/agent/skills/tui-test

# Or manage it through the skills library (copies the skill into the store)
skills-manager-cli skills install /abs/path/to/mcp-tui-test/skills/tui-test --local --name tui-test
skills-manager-cli skills deploy --agent omp_agent tui-test
```

Verify discovery from any directory with `omp read skill://tui-test` (it prints the `SKILL.md` body);
inside a session, `/skill:tui-test` injects the same content.

## No mount (CI, or another agent's session)

`run_scenario` is reachable in-process through the same `mcp.call_tool(...)` dispatch the mounted
tools use, so a scenario still runs when nothing is mounted. Pass the document itself — the tool finds
its single python block, so there is no extraction step to keep in sync:

```bash
uv run --project /abs/path/to/mcp-tui-test python - <<'PY'
import asyncio, pathlib
from mcp_tui_test._server import mcp

doc = pathlib.Path("docs/guides/tui/<datetime>-scenario-<label>.md").read_text()

async def main():
    result = await mcp.call_tool("run_scenario", {"script": doc, "session_id": "<label>"})
    print(result.content[0].text)              # rendered report -> the transcript file
    print(result.structured_content["ok"])     # machine-readable verdict

asyncio.run(main())
PY
```

Save that report verbatim as
`docs/guides/tui/<datetime>-scenario-<label>-transcript.md`. Both a lint error and a failed step set
`ok: false`, so it works as a CI gate.

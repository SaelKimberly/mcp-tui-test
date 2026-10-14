#!/usr/bin/env python3
"""Colour fixture for mcp-tui-test: a static screen exercising colour and attributes.

Every screen row is fixed, so a test can assert an exact cell:

| Row | Content |
|---|---|
| 0 | `BOLD` (bold, default colour) |
| 1 | `ANSI` red fg, then `BG` green bg + black fg |
| 2 | `C256` 256-colour fg `196` (ff0000), `G256` 256-colour fg `46` (00ff00) |
| 3 | `TRUE` true-colour fg `10;200;30` (0ac81e) |
| 4 | `UNDER` underline, `STRIKE` strikethrough, `ITAL` italic, `REV` reverse |
| 5 | `Press Enter to quit` |

The process stays alive until Enter, so a buffer-mode session can read and
screenshot it.
"""

import sys

RESET = "\x1b[0m"

ROWS = [
    "\x1b[1mBOLD\x1b[22m",
    "\x1b[31mANSI\x1b[39m \x1b[42;30mBG\x1b[49;39m",
    "\x1b[38;5;196mC256\x1b[39m \x1b[38;5;46mG256\x1b[39m",
    "\x1b[38;2;10;200;30mTRUE\x1b[39m",
    "\x1b[4mUNDER\x1b[24m \x1b[9mSTRIKE\x1b[29m \x1b[3mITAL\x1b[23m \x1b[7mREV\x1b[27m",
    "Press Enter to quit",
]


def render() -> str:
    """The screen as one string (trailing reset, newline-terminated rows)."""
    return "\n".join(ROWS) + RESET + "\n"


def main() -> None:
    sys.stdout.write(render())
    sys.stdout.flush()
    try:
        sys.stdin.readline()
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scenario script parsing, validation, and reporting for the tui-test server.

A scenario script is Python-like source made of bare step calls::

    launch("env TERM=xterm-256color LC_ALL=C nano notes.txt", mode="buffer")
    expect("GNU nano", timeout=5)
    send("hello")
    assert_text("hello")
    close()

:func:`parse_script` reads that source with :mod:`ast` and resolves it into an execution pipeline of
real tui-test tool calls. **The source is never executed** — only literals are read out of the syntax
tree, and anything that is not a whitelisted step call with literal arguments becomes a diagnostic.

This module touches no session, pexpect, or pyte state, so the whole validator is unit-testable
without spawning a subprocess. The server owns execution (`run_scenario` in ``_server.py``).
"""

from __future__ import annotations

import ast
import difflib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# A `choice` epilogue may only contain assertions: instructions whose failure means "this label did
# not match" rather than "the scenario broke".
CHOICE_STEPS = frozenset({"expect", "assert_text", "assert_at"})

# Reads that only make sense with pyte's screen buffer behind them.
BUFFER_ONLY_STEPS = frozenset({"line", "region", "char", "cursor", "assert_at"})

FALLBACK_SPECIAL_KEYS = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "page_up",
        "page_down",
        "backspace",
        "delete",
        "insert",
        "enter",
        "tab",
        "escape",
        *[f"f{n}" for n in range(1, 13)],
    }
)


@dataclass(frozen=True, slots=True)
class Param:
    """One accepted step argument."""

    name: str
    kind: type | tuple[type, ...]
    doc: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class StepSpec:
    """A DSL step: the tool it maps onto, and the arguments it accepts."""

    tool: str
    summary: str
    positional: tuple[Param, ...] = ()
    keyword: tuple[Param, ...] = ()
    variadic: Param | None = None


def _p(name: str, kind: type | tuple[type, ...], doc: str = "", default: str = "") -> Param:
    return Param(name=name, kind=kind, doc=doc, default=default)


STEP_SPECS: dict[str, StepSpec] = {
    "launch": StepSpec(
        tool="launch_tui",
        summary="Start the TUI under test (replaces an attached session with the same id).",
        positional=(_p("command", str, "shell command to run"),),
        keyword=(
            _p("timeout", int, "seconds before the child is killed", "30"),
            _p("dimensions", str, "terminal size as WxH", "'80x24'"),
            _p("mode", str, "'buffer' for full TUIs, 'stream' for line-oriented CLI tools", "'buffer'"),
            _p("env", dict, "extra environment variables for the child"),
        ),
    ),
    "send": StepSpec(
        tool="send_keys",
        summary=r"Send keystrokes; escapes are decoded, so use \r for Enter.",
        positional=(_p("keys", str),),
        keyword=(
            _p("delay", float, "seconds to wait after sending", "0.3"),
            _p("inter_key_delay", float, "seconds between characters", "0.0"),
        ),
    ),
    "ctrl": StepSpec(
        tool="send_ctrl",
        summary="Send Ctrl+<letter>.",
        positional=(_p("key", str, "single letter, e.g. 'o'"),),
    ),
    "key": StepSpec(
        tool="send_special_keys",
        summary="Send named special keys (up, down, enter, escape, f1..f12, ...).",
        variadic=_p("keys", str, "one or more special key names"),
        keyword=(_p("delay", float, "seconds to wait after the sequence", "0.3"),),
    ),
    "expect": StepSpec(
        tool="expect_text",
        summary="Wait for a regex to appear on screen (primary synchronization primitive).",
        positional=(_p("pattern", str, "literal text or regex"),),
        keyword=(_p("timeout", int, "seconds to wait", "10"),),
    ),
    "assert_text": StepSpec(
        tool="assert_contains",
        summary="Assert the whole screen contains a substring.",
        positional=(_p("text", str),),
    ),
    "assert_at": StepSpec(
        tool="assert_at_position",
        summary="Assert text starts at a screen position (whitespace-insensitive compare).",
        positional=(_p("text", str), _p("row", int), _p("col", int)),
    ),
    "line": StepSpec(
        tool="get_line",
        summary="Read one screen row (0-indexed; row 0 is usually the title bar).",
        positional=(_p("row", int),),
    ),
    "region": StepSpec(
        tool="get_screen_region",
        summary="Read a rectangular region (row_end exclusive).",
        positional=(_p("row_start", int), _p("row_end", int)),
        keyword=(_p("col_start", int, default="0"), _p("col_end", int)),
    ),
    "char": StepSpec(
        tool="get_char",
        summary="Read a single cell — the only exact column-width check.",
        positional=(_p("row", int), _p("col", int)),
    ),
    "cursor": StepSpec(
        tool="get_cursor_position",
        summary="Read the cursor position.",
    ),
    "capture": StepSpec(
        tool="capture_screen",
        summary="Snapshot the whole screen (the buffer text arrives wrapped in a header and a rule).",
        keyword=(
            _p("include_ansi", bool, "stream mode only"),
            _p("preserve_format", bool, "keep trailing padding for exact layout"),
        ),
    ),
    "stable": StepSpec(
        tool="wait_for_stable",
        summary="Wait until consecutive reads stop changing.",
        keyword=(
            _p("interval", float, default="0.1"),
            _p("consecutive", int, default="3"),
            _p("timeout", int, default="10"),
        ),
    ),
    "change": StepSpec(
        tool="wait_for_change",
        summary="Wait until the screen differs from a bare-text reference.",
        positional=(_p("reference", str, "bare buffer text, without the capture frame"),),
        keyword=(_p("interval", float, default="0.1"), _p("timeout", int, default="10")),
    ),
    "info": StepSpec(tool="get_session_info", summary="Session metadata (command, mode, PID, uptime)."),
    "sessions": StepSpec(tool="list_sessions", summary="List active sessions."),
    "close": StepSpec(tool="close_session", summary="Kill the child and drop the session."),
}

_TOOL_TO_STEP = {spec.tool: name for name, spec in STEP_SPECS.items()}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One lint message, anchored to the script source."""

    severity: str  # "error" | "warning"
    code: str
    line: int
    col: int
    message: str
    hint: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True, slots=True)
class Step:
    """A resolved pipeline step: a DSL call mapped onto a tool call."""

    name: str
    tool: str
    arguments: dict[str, object]
    line: int
    # Only the `choice` epilogue carries these: (label, instruction) pairs, in written order.
    alternatives: tuple[tuple[str, Step], ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    """Parse result: the resolved pipeline plus every diagnostic found."""

    steps: tuple[Step, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    launches: bool = False
    stream_mode: bool = False
    closes: bool = False
    # Frontmatter of a scenario document, as (key, value) pairs; empty for an ad-hoc script.
    header: tuple[tuple[str, str], ...] = ()

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.is_error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if not d.is_error)

    @property
    def ok(self) -> bool:
        return not self.errors

    def payload(self) -> list[dict[str, object]]:
        """The pipeline as JSON-friendly data; session_id is injected at execution time."""
        return [
            {
                "step": step.name,
                "tool": step.tool,
                "arguments": dict(step.arguments),
                "line": step.line,
                **(
                    {
                        "alternatives": [
                            {"label": label, "step": inner.name, "tool": inner.tool, "arguments": dict(inner.arguments)}
                            for label, inner in step.alternatives
                        ]
                    }
                    if step.alternatives
                    else {}
                ),
            }
            for step in self.steps
        ]


class _NotLiteral:
    """Sentinel: the node was not a literal."""


class _BadDictKey:
    """Sentinel: a dict literal whose key is a literal but not a string."""


_CONTROL_MAP = {code: f"\\x{code:02x}" for code in range(0x20)} | {0x7F: "\\x7f"}


def one_line(text: object) -> str:
    """Render text on a single line, escaping control characters.

    Diagnostics and report headers interpolate values an agent chose (special-key names, a session
    id). Without this, a name containing a decoded newline could print a fabricated line such as
    ``✓ ALL CHECKS PASSED`` into a report a reader trusts.
    """
    return str(text).translate(_CONTROL_MAP)


_NOT_LITERAL = _NotLiteral()
_BAD_DICT_KEY = _BadDictKey()


class _Anchor:
    """Position-only stand-in so a diagnostic can point at a line without a real node."""

    def __init__(self, lineno: int, col_offset: int = 0) -> None:
        self.lineno = lineno
        self.col_offset = col_offset


def _position(node: object) -> tuple[int, int]:
    """Source position of a node; the bare ``ast.AST`` base class does not declare one."""
    return int(getattr(node, "lineno", 1) or 1), int(getattr(node, "col_offset", 0) or 0)


def _literal_value(node: ast.AST) -> object:
    """Literal value of a node, or ``_NOT_LITERAL``."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = []
        for element in node.elts:
            value = _literal_value(element)
            if isinstance(value, _NotLiteral):
                return _NOT_LITERAL
            values.append(value)
        return values
    if isinstance(node, ast.Dict):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            key = _literal_value(key_node) if key_node is not None else _NOT_LITERAL
            value = _literal_value(value_node)
            if isinstance(key, _NotLiteral) or isinstance(value, _NotLiteral):
                return _NOT_LITERAL
            if not isinstance(key, str):
                return _BAD_DICT_KEY
            result[key] = value
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _literal_value(node.operand)
        if isinstance(operand, (int, float)) and not isinstance(operand, bool):
            return -operand
    return _NOT_LITERAL


class _Validator:
    def __init__(self, source: str, special_keys: frozenset[str]) -> None:
        self.source = source
        self.source_lines = source.splitlines()
        self.special_keys = special_keys
        self.diagnostics: list[Diagnostic] = []
        self.steps: list[Step] = []
        self.launched = False
        self.stream_mode = False
        self.buffer_mode = False
        self.closed = False
        self.choice_line: int | None = None

    # -- diagnostics ---------------------------------------------------------
    def error(self, code: str, node: object, message: str, hint: str = "") -> None:
        line, col = _position(node)
        self.diagnostics.append(Diagnostic("error", code, line, col, message, hint))

    def warn(self, code: str, node: object, message: str, hint: str = "") -> None:
        line, col = _position(node)
        self.diagnostics.append(Diagnostic("warning", code, line, col, message, hint))

    @staticmethod
    def _suggest_name(name: str) -> str:
        pool = {**{step: step for step in STEP_SPECS}, **_TOOL_TO_STEP}
        close = difflib.get_close_matches(name, list(pool), n=1, cutoff=0.6)
        if not close:
            return ""
        match = close[0]
        if match in STEP_SPECS:
            return f"did you mean '{match}'?"
        return f"did you mean '{pool[match]}' (the tool is {match})?"

    def _keyword_anchor(self, keyword: ast.keyword) -> _Anchor:
        """Anchor at the argument name — that is the text the reader has to change."""
        line = getattr(keyword.value, "lineno", 1)
        value_col = getattr(keyword.value, "col_offset", 0)
        text = self.source_lines[line - 1] if 0 < line <= len(self.source_lines) else ""
        col = text.rfind(keyword.arg or "", 0, value_col)
        return _Anchor(line, col if col >= 0 else value_col)

    @staticmethod
    def _suggest_param(name: str, known: set[str]) -> str:
        close = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.6)
        if close:
            return f"did you mean '{close[0]}'?"
        return "valid arguments: " + ", ".join(sorted(known))

    @staticmethod
    def _usage(name: str, spec: StepSpec) -> str:
        parts = [p.name for p in spec.positional]
        if spec.variadic is not None:
            parts.append(f"{spec.variadic.name}...")
        parts += [f"{p.name}={p.default or '...'}" for p in spec.keyword]
        return f"{name}({', '.join(parts)})"

    # -- literal handling ----------------------------------------------------
    def _literal(self, node: ast.AST, step_name: str, param: Param) -> tuple[bool, object]:
        value = _literal_value(node)
        if isinstance(value, _BadDictKey):
            self.error(
                "E_ARG_TYPE",
                node,
                f"'{param.name}' of {step_name}() must be a dict with string keys",
                'mapping arguments use string keys, e.g. env={"TERM": "xterm-256color"}',
            )
            return False, None
        if isinstance(value, _NotLiteral):
            self.error(
                "E_ARG_NOT_LITERAL",
                node,
                f"'{param.name}' of {step_name}() must be a literal (string, number, bool, list, or dict)",
                "compute values outside the script — a scenario must replay verbatim",
            )
            return False, None
        return True, value

    def _check_type(self, node: object, step_name: str, param: Param, value: object) -> bool:
        expected = param.kind if isinstance(param.kind, tuple) else (param.kind,)
        if isinstance(value, bool):
            ok = bool in expected
        elif isinstance(value, int) and float in expected:
            ok = True
        else:
            ok = isinstance(value, expected)
        if not ok:
            names = " or ".join(t.__name__ for t in expected)
            self.error("E_ARG_TYPE", node, f"{step_name}('{param.name}') expects {names}, got {type(value).__name__}")
        return ok

    # -- traversal -----------------------------------------------------------
    def run(self, tree: ast.Module) -> None:
        if not tree.body:
            self.diagnostics.append(Diagnostic("error", "E_EMPTY_SCRIPT", 1, 0, "scenario script is empty"))
        for statement in tree.body:
            self.visit_statement(statement)
        self.order_checks()

    def visit_statement(self, statement: ast.AST) -> None:
        if not isinstance(statement, ast.Expr):
            self.error(
                "E_NOT_A_STEP_CALL",
                statement,
                f"{type(statement).__name__} is not allowed in a scenario",
                "a scenario is a sequence of bare step calls — no assignments, imports, loops, or helpers",
            )
            return
        call = statement.value
        if not isinstance(call, ast.Call):
            self.error(
                "E_NOT_A_STEP_CALL",
                statement,
                "only a step call can stand alone as a statement",
                "wrap the value in a step, e.g. expect(...) or assert_text(...)",
            )
            return
        if not isinstance(call.func, ast.Name):
            label = getattr(call.func, "attr", None) or ast.unparse(call.func)
            self.error(
                "E_ATTR_CALL",
                call,
                f"'{label}' is not a step — only bare step calls are allowed",
                "step names are plain identifiers: " + ", ".join(sorted(STEP_SPECS)),
            )
            return
        self.visit_step(call.func, call)

    def visit_step(self, func: ast.Name, call: ast.Call) -> None:
        name = func.id
        if name == "choice":
            if self.closed:
                self.error(
                    "E_UNREACHABLE",
                    call,
                    "choice() comes after close(); that session is already gone",
                    "drop close() — choice() is the epilogue, and run_scenario() closes the session it launched",
                )
                return
            if self.choice_line is not None:
                self.error(
                    "E_MULTIPLE_CHOICE",
                    call,
                    "a scenario may hold only one choice() epilogue",
                    "merge the labels into a single choice()",
                )
                return
            alternatives = self.collect_choice(call)
            if alternatives is None:
                return
            self.choice_line = call.lineno
            self.steps.append(Step(name="choice", tool="choice", arguments={}, line=call.lineno, alternatives=alternatives))
            return
        if self.choice_line is not None:
            self.error(
                "E_AFTER_CHOICE",
                call,
                f"{name}() comes after choice(); the epilogue must be the last instruction",
                "move choice() to the end — it reports the outcome and the tool closes the session",
            )
            return
        spec = STEP_SPECS.get(name)
        if spec is None:
            self.error(
                "E_UNKNOWN_STEP",
                call,
                f"unknown step '{name}'",
                self._suggest_name(name) or "valid steps: " + ", ".join(sorted(STEP_SPECS)),
            )
            return
        if self.closed and name != "launch":
            self.error(
                "E_UNREACHABLE",
                call,
                f"{name}() comes after close() and can never run",
                "move close() to the end of the script, or launch() again to start another app",
            )
            return
        step = self._resolve_step(func, call)
        if step is None:
            return
        arguments = step.arguments
        self.steps.append(step)
        if name == "launch":
            self.launched = True
            mode = arguments.get("mode", "buffer")
            self.stream_mode = mode == "stream"
            self.buffer_mode = mode == "buffer"
            # A launch after close() starts a second app in the same scenario: later steps run again.
            self.closed = False
        elif name == "close":
            self.closed = True

    def _resolve_step(self, func: ast.Name, call: ast.Call) -> Step | None:
        """Validate one instruction call; shared by top-level steps and choice() alternatives."""
        name = func.id
        spec = STEP_SPECS.get(name)
        if spec is None:
            self.error(
                "E_UNKNOWN_STEP",
                call,
                f"unknown step '{name}'",
                self._suggest_name(name) or "valid steps: " + ", ".join(sorted(STEP_SPECS)),
            )
            return None
        arguments = self.collect_arguments(call, name, spec)
        if arguments is None:
            return None
        if not self.value_checks(call, name, arguments):
            return None
        self.semantic_checks(call, name, arguments)
        return Step(name=name, tool=spec.tool, arguments=arguments, line=call.lineno)

    def collect_choice(self, call: ast.Call) -> tuple[tuple[str, Step], ...] | None:
        """Validate the choice() dict: labeled assertion instructions, in written order."""
        usage = 'usage: choice({"label": expect("...", timeout=5), "other": assert_text("...")})'
        if call.keywords or len(call.args) != 1 or not isinstance(call.args[0], ast.Dict):
            self.error("E_CHOICE_ARGUMENT", call, "choice() takes a single dict literal of labeled instructions", usage)
            return None
        mapping = call.args[0]
        if not mapping.keys:
            self.error("E_CHOICE_EMPTY", call, "choice() needs at least one alternative", usage)
            return None

        alternatives: list[tuple[str, Step]] = []
        seen: set[str] = set()
        fine = True
        budget = 0.0
        for key_node, value_node in zip(mapping.keys, mapping.values):
            label = _literal_value(key_node) if key_node is not None else _NOT_LITERAL
            if isinstance(label, (_NotLiteral, _BadDictKey)) or not isinstance(label, str) or not label.strip():
                self.error(
                    "E_CHOICE_LABEL",
                    key_node if key_node is not None else call,
                    "choice() labels must be non-empty string literals",
                    "the label is what the report shows when that alternative matches",
                )
                fine = False
                continue
            if label in seen:
                self.error("E_CHOICE_LABEL", key_node, f"duplicate choice() label {label!r}")
                fine = False
                continue
            seen.add(label)
            if not isinstance(value_node, ast.Call) or not isinstance(value_node.func, ast.Name):
                self.error(
                    "E_CHOICE_INSTRUCTION",
                    value_node,
                    f"choice({label!r}) must be an instruction call",
                    "allowed inside choice(): " + ", ".join(sorted(CHOICE_STEPS)),
                )
                fine = False
                continue
            if value_node.func.id not in STEP_SPECS:
                self.error(
                    "E_CHOICE_INSTRUCTION",
                    value_node,
                    f"choice({label!r}) calls '{value_node.func.id}', which is not an instruction",
                    "allowed inside choice(): " + ", ".join(sorted(CHOICE_STEPS)),
                )
                fine = False
                continue
            inner = self._resolve_step(value_node.func, value_node)
            if inner is None:
                fine = False
                continue
            if inner.name not in CHOICE_STEPS:
                self.error(
                    "E_CHOICE_INSTRUCTION",
                    value_node,
                    f"{inner.name}() cannot be used inside choice()",
                    "allowed inside choice(): " + ", ".join(sorted(CHOICE_STEPS)),
                )
                fine = False
                continue
            if inner.name == "expect":
                # Every alternative is evaluated, so a waiting alternative costs its full timeout even
                # when another one matches first.
                budget += float(inner.arguments.get("timeout", 10))
            alternatives.append((label, inner))

        if fine and self.stream_mode and any(inner.name == "expect" for _, inner in alternatives):
            self.warn(
                "W_CHOICE_STREAM_EXPECT",
                call,
                "a stream-mode expect() consumes the pexpect accumulator, so an alternative evaluated "
                "later cannot see text an earlier one already matched",
                "use buffer mode for a choice() epilogue, or assert cheaply with assert_text()",
            )
        if fine and budget > 300:
            self.warn(
                "W_LONG_BUDGET",
                call,
                f"choice() alternatives can wait up to {budget:.0f}s in total; every alternative is evaluated",
                "prefer assert_text()/assert_at() for the cheap cases and shorten expect() timeouts",
            )
        return tuple(alternatives) if fine else None

    def _params_in_order(self, spec: StepSpec) -> tuple[Param, ...]:
        extra = (spec.variadic,) if spec.variadic is not None else ()
        return (*spec.positional, *extra, *spec.keyword)

    def collect_arguments(self, call: ast.Call, name: str, spec: StepSpec) -> dict[str, object] | None:
        arguments: dict[str, object] = {}
        supplied: set[str] = set()
        fine = True
        positional = list(spec.positional)

        for index, node in enumerate(call.args):
            if isinstance(node, ast.Starred):
                self.error("E_ARG_STARRED", node, f"*args is not allowed in {name}()", "list every argument literally")
                fine = False
                continue
            if spec.variadic is not None and index >= len(positional):
                supplied.add(spec.variadic.name)
                ok, value = self._literal(node, name, spec.variadic)
                if ok and self._check_type(node, name, spec.variadic, value):
                    arguments.setdefault(spec.variadic.name, []).append(value)
                else:
                    fine = False
                continue
            if index >= len(positional):
                extra = ", ".join(p.name for p in spec.keyword) or "none"
                self.error(
                    "E_ARG_TOO_MANY_POSITIONAL",
                    node,
                    f"{name}() takes {len(positional)} positional argument(s); got {len(call.args)}",
                    f"remaining arguments are keywords: {extra}",
                )
                fine = False
                continue
            param = positional[index]
            supplied.add(param.name)
            ok, value = self._literal(node, name, param)
            if ok and self._check_type(node, name, param, value):
                arguments[param.name] = value
            else:
                fine = False

        known = {p.name for p in self._params_in_order(spec)}
        for keyword in call.keywords:
            anchor = self._keyword_anchor(keyword)
            if keyword.arg is None:
                self.error("E_ARG_KW_STAR", keyword.value, f"**kwargs is not allowed in {name}()", "pass every argument literally")
                fine = False
                continue
            param_name = keyword.arg
            if param_name == "session_id":
                self.error(
                    "E_SESSION_ARG",
                    anchor,
                    "session_id is not a scenario argument",
                    "pass session_id to run_scenario(); it is applied to every step",
                )
                fine = False
                continue
            supplied.add(param_name)
            if param_name not in known:
                self.error("E_ARG_UNKNOWN", anchor, f"{name}() has no argument '{param_name}'", self._suggest_param(param_name, known))
                fine = False
                continue
            if param_name in arguments:
                self.error("E_ARG_DUPLICATE", anchor, f"'{param_name}' is passed twice to {name}()")
                fine = False
                continue
            param = next(p for p in self._params_in_order(spec) if p.name == param_name)
            supplied.add(param_name)
            ok, value = self._literal(keyword.value, name, param)
            if ok and param is spec.variadic:
                values = list(value) if isinstance(value, list) else [value]
                if not values:
                    self.error(
                        "E_ARG_MISSING",
                        keyword.value,
                        f"{name}() needs at least one {param.name}",
                        f"usage: {self._usage(name, spec)}",
                    )
                    fine = False
                elif all(self._check_type(keyword.value, name, param, item) for item in values):
                    arguments[param_name] = values
                else:
                    fine = False
            elif ok and self._check_type(keyword.value, name, param, value):
                arguments[param_name] = value
            else:
                fine = False

        for param in spec.positional:
            if param.name not in arguments and param.name not in supplied:
                self.error(
                    "E_ARG_MISSING",
                    call,
                    f"{name}() is missing required argument '{param.name}'",
                    f"usage: {self._usage(name, spec)}",
                )
                fine = False
        if spec.variadic is not None and spec.variadic.name not in arguments and spec.variadic.name not in supplied:
            self.error(
                "E_ARG_MISSING",
                call,
                f"{name}() needs at least one {spec.variadic.name}",
                f"usage: {self._usage(name, spec)}",
            )
            fine = False
        return arguments if fine else None

    # -- values that would otherwise only fail at runtime ---------------------
    def value_checks(self, call: ast.Call, name: str, arguments: dict[str, object]) -> bool:
        """Reject enumerated/structured values the linter's own reasoning depends on."""
        ok = self.timing_checks(call, name, arguments)
        if name != "launch":
            return ok
        mode = arguments.get("mode", "buffer")
        if mode not in ("buffer", "stream"):
            self.error(
                "E_ARG_VALUE",
                call,
                f"launch(mode={mode!r}) is not a valid mode",
                "valid modes: 'buffer' (full-screen TUIs) or 'stream' (line-oriented CLI tools)",
            )
            ok = False
        dimensions = arguments.get("dimensions")
        if dimensions is not None:
            size = str(dimensions)
            if not re.fullmatch(r"\d+x\d+", size):
                self.error(
                    "E_ARG_VALUE",
                    call,
                    f"launch(dimensions={size!r}) is not a valid terminal size",
                    "expected WxH, e.g. '120x40'",
                )
                ok = False
            elif not all(1 <= int(side) <= 65535 for side in size.split("x")):
                self.error(
                    "E_ARG_VALUE",
                    call,
                    f"launch(dimensions={size!r}) is outside the supported range",
                    "width and height must be between 1 and 65535",
                )
                ok = False
        return ok

    def timing_checks(self, call: ast.Call, name: str, arguments: dict[str, object]) -> bool:
        """Keep a legal script from blocking a server worker for an unbounded time."""
        ok = True
        for param in ("delay", "interval"):
            value = arguments.get(param)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                self.error("E_ARG_VALUE", call, f"{name}({param}={value}) is negative")
                ok = False
        for param in ("timeout", "consecutive"):
            value = arguments.get(param)
            if isinstance(value, int) and not isinstance(value, bool) and value < 1:
                self.error("E_ARG_VALUE", call, f"{name}({param}={value}) must be at least 1")
                ok = False
        for param in ("timeout", "delay", "interval"):
            value = arguments.get(param)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 300:
                self.warn(
                    "W_LONG_BUDGET",
                    call,
                    f"{name}({param}={value}) can block this tool call for a long time",
                    "a scenario is not sandboxed: keep per-step waits short and assert between them",
                )
        return ok

    # -- semantics that would otherwise surface only at runtime ---------------
    def semantic_checks(self, call: ast.Call, name: str, arguments: dict[str, object]) -> None:
        if name == "expect":
            try:
                _ = re.compile(str(arguments["pattern"]))
            except re.error as exc:
                self.error(
                    "E_REGEX_INVALID",
                    call,
                    f"expect() pattern is not a valid regex: {exc}",
                    "escape regex metacharacters (\\[, \\(, \\d) or match literal text with assert_text()",
                )
        if name == "key":
            supplied = arguments.get("keys", [])
            unknown = [k for k in supplied if isinstance(k, str) and k not in self.special_keys]
            if unknown:
                self.error(
                    "E_SPECIAL_KEY",
                    call,
                    f"unknown special key(s): {', '.join(one_line(key) for key in unknown)}",
                    "valid names: " + ", ".join(sorted(self.special_keys)),
                )
        if name == "ctrl":
            key = arguments.get("key", "")
            if not (len(key) == 1 and key.isalpha()):
                self.error(
                    "E_CTRL_KEY",
                    call,
                    f"ctrl() takes a single letter, got {key!r}",
                    r"use key('enter'), key('escape') or send('\r') for named keys",
                )
        if name == "send" and self.buffer_mode and "\n" in arguments.get("keys", ""):
            self.warn(
                "W_ENTER_ESCAPE",
                call,
                r"send() keys contain '\n'; raw-mode TUIs expect '\r' for Enter",
                r"use send('q\r') or key('enter')",
            )
        if name == "change":
            reference = arguments.get("reference", "")
            if "=" * 60 in reference or reference.startswith("Screen output"):
                self.warn(
                    "W_CHANGE_REFERENCE_FRAMED",
                    call,
                    "change() reference looks like a framed capture; its header alone counts as a change",
                    "pass bare buffer text (strip the header and the '=' rule from a capture first)",
                )
        if name in BUFFER_ONLY_STEPS and self.stream_mode:
            self.warn(
                "W_STREAM_MODE_READ",
                call,
                f"{name}() is meaningless on a stream-mode session",
                "launch(...) with mode='buffer' for position-aware reads",
            )
        if name in BUFFER_ONLY_STEPS:
            for param in ("row", "row_start", "row_end", "col", "col_start", "col_end"):
                value = arguments.get(param)
                if isinstance(value, int) and value < 0:
                    self.error(
                        "E_NEGATIVE_POSITION",
                        call,
                        f"{name}({param}={value}) is negative; screen coordinates are 0-based",
                    )

    def order_checks(self) -> None:
        if not self.steps:
            return
        anchor = _Anchor(self.steps[0].line)
        if not self.launched:
            self.warn(
                "W_NO_LAUNCH",
                anchor,
                "no launch() step: the script will attach to a live session",
                "add launch(...) first when the script must stand alone",
            )
        elif self.steps[0].name != "launch":
            self.warn(
                "W_LAUNCH_NOT_FIRST",
                anchor,
                f"the first step is {self.steps[0].name}(), not launch()",
                "steps before launch() only work when attaching to a live session",
            )
            self.warn(
                "W_LAUNCH_REPLACES_SESSION",
                anchor,
                "the later launch() replaces the attached session with the same id",
            )
        if not self.closed and self.choice_line is None:
            # With a choice() epilogue the script cannot also close(): nothing may follow it, and the
            # tool's own cleanup owns the session.
            self.warn(
                "W_NO_CLOSE",
                anchor,
                "the script never calls close()",
                "run_scenario() closes a session it launched, but keeps an attached session alive",
            )


def parse_script(source: str, *, special_keys: frozenset[str] = FALLBACK_SPECIAL_KEYS) -> Plan:
    """Parse a scenario script into a pipeline plus diagnostics. The source is never executed."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        # ValueError covers UnicodeEncodeError on lone surrogates, which is raised instead of a
        # SyntaxError; both must come back as diagnostics, never as a traceback out of the tool.
        message = " ".join(str(exc).split())
        diagnostic = Diagnostic(
            "error",
            "E_SYNTAX",
            getattr(exc, "lineno", None) or 1,
            (getattr(exc, "offset", None) or 1) - 1,
            f"scenario does not parse: {message}",
        )
        return Plan(diagnostics=(diagnostic,))
    validator = _Validator(source, special_keys)
    validator.run(tree)
    diagnostics = tuple(sorted(validator.diagnostics, key=lambda d: (d.line, d.col, d.code)))
    return Plan(
        steps=tuple(validator.steps),
        diagnostics=diagnostics,
        launches=validator.launched,
        stream_mode=validator.stream_mode,
        closes=validator.closed,
    )


_FENCE = re.compile(r"^\s*```(\S*)\s*$")


def parse_document(document: str, *, special_keys: frozenset[str] = FALLBACK_SPECIAL_KEYS) -> Plan:
    """Parse a markdown scenario document: a header block plus exactly one ```python block.

    Other fenced blocks are documentation and are ignored. Diagnostics from the code block are shifted
    so their line numbers and carets refer to the document the author wrote.
    """
    header, body_start, diagnostics = _split_frontmatter(document)
    blocks, fence_diagnostics = _python_blocks(document)
    diagnostics.extend(fence_diagnostics)

    if not blocks:
        diagnostics.append(
            Diagnostic(
                "error",
                "E_NO_PYTHON_BLOCK",
                body_start + 1,
                0,
                "scenario document needs exactly one ```python block",
                "put the step calls in a single fenced python block; other fences are ignored",
            )
        )
        return Plan(diagnostics=tuple(sorted(diagnostics, key=lambda d: (d.line, d.col, d.code))), header=tuple(header.items()))
    if len(blocks) > 1:
        diagnostics.append(
            Diagnostic(
                "error",
                "E_MULTIPLE_PYTHON_BLOCKS",
                blocks[1][0],
                0,
                f"scenario document has {len(blocks)} python blocks; exactly one is allowed",
                "python blocks start at lines " + ", ".join(str(start) for start, _ in blocks),
            )
        )
        return Plan(diagnostics=tuple(sorted(diagnostics, key=lambda d: (d.line, d.col, d.code))), header=tuple(header.items()))

    start_line, code = blocks[0]
    plan = parse_script(code, special_keys=special_keys)
    offset = start_line - 1
    shifted = tuple(
        Diagnostic(d.severity, d.code, d.line + offset, d.col, d.message, d.hint) for d in plan.diagnostics
    )
    merged = sorted((*diagnostics, *shifted), key=lambda d: (d.line, d.col, d.code))
    return Plan(
        steps=tuple(_shift_step(step, offset) for step in plan.steps),
        diagnostics=tuple(merged),
        launches=plan.launches,
        stream_mode=plan.stream_mode,
        closes=plan.closes,
        header=tuple(header.items()),
    )


def _shift_step(step: Step, offset: int) -> Step:
    """Re-anchor a resolved step on the document's line numbers.

    Diagnostics from a code block are shifted into document coordinates; the pipeline must agree, or
    `pipeline[].line` would point at the block's own first line while the report points at the page.
    """
    return Step(
        name=step.name,
        tool=step.tool,
        arguments=dict(step.arguments),
        line=step.line + offset,
        alternatives=tuple((label, _shift_step(inner, offset)) for label, inner in step.alternatives),
    )


def _split_frontmatter(document: str) -> tuple[dict[str, str], int, list[Diagnostic]]:
    """Read the leading `---` block: `key: value` lines, inline `[a, b]` lists allowed."""
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        return (
            {},
            0,
            [
                Diagnostic(
                    "error",
                    "E_MISSING_HEADER",
                    1,
                    0,
                    "scenario document needs a header",
                    "start the file with:\n---\nname: <label>\ndescription: <what this scenario proves>\n---",
                )
            ],
        )
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return (
            {},
            0,
            [Diagnostic("error", "E_HEADER_SYNTAX", 1, 0, "header block is never closed", "add a closing '---' line")],
        )

    fields: dict[str, str] = {}
    diagnostics: list[Diagnostic] = []
    for index in range(1, end):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, separator, value = raw.partition(":")
        if not separator or not key.strip() or " " in key.strip():
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E_HEADER_SYNTAX",
                    index + 1,
                    0,
                    f"cannot read header line: {one_line(raw.strip())!r}",
                    "use 'key: value'; lists look like [a, b]",
                )
            )
            continue
        fields[key.strip()] = value.strip().strip('"').strip("'")
    for required in ("name", "description"):
        if not fields.get(required):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E_HEADER_FIELD",
                    1,
                    0,
                    f"header is missing '{required}'",
                    "name is the label an agent searches for; description is how it decides to reuse this scenario",
                )
            )
    return fields, end + 1, diagnostics


def _python_blocks(document: str) -> tuple[list[tuple[int, str]], list[Diagnostic]]:
    """Every ```python fenced block as (line number of its first code line, code)."""
    blocks: list[tuple[int, str]] = []
    diagnostics: list[Diagnostic] = []
    lines = document.splitlines()
    index = 0
    while index < len(lines):
        match = _FENCE.match(lines[index])
        if not match:
            index += 1
            continue
        info = match.group(1)
        start = index + 2
        body: list[str] = []
        index += 1
        closed = False
        while index < len(lines):
            if _FENCE.match(lines[index]):
                closed = True
                break
            body.append(lines[index])
            index += 1
        if not closed:
            diagnostics.append(
                Diagnostic("error", "E_UNTERMINATED_BLOCK", start - 1, 0, f"'{info or 'code'}' block is never closed")
            )
            break
        if info.startswith("python"):
            blocks.append((start, "\n".join(body)))
        index += 1
    return blocks, diagnostics


def render_diagnostics(diagnostics: tuple[Diagnostic, ...] | list[Diagnostic], source: str) -> str:
    """Render lint messages with a source excerpt and caret, IDE-style."""
    lines = source.splitlines()
    blocks: list[str] = []
    for diagnostic in diagnostics:
        header = f"{diagnostic.line}:{diagnostic.col + 1}: {diagnostic.severity}: {diagnostic.code}: {diagnostic.message}"
        excerpt: list[str] = []
        text = lines[diagnostic.line - 1] if 0 < diagnostic.line <= len(lines) else ""
        if text.strip():
            excerpt = [f"    {text}", f"    {' ' * diagnostic.col}^"]
        hint = [f"    hint: {diagnostic.hint}"] if diagnostic.hint else []
        blocks.extend([header, *excerpt, *hint])
    return "\n".join(blocks)


def render_pipeline(steps: tuple[Step, ...] | list[Step]) -> str:
    """Render the resolved pipeline as the canonical script form, for review or paste-back."""
    lines = []
    for index, step in enumerate(steps, start=1):
        rendered = [f"{key}={value!r}" for key, value in step.arguments.items()]
        lines.append(f"{index:>2}. {step.name}({', '.join(rendered)})  # {step.tool}")
        for label, inner in step.alternatives:
            inner_rendered = ", ".join(f"{key}={value!r}" for key, value in inner.arguments.items())
            lines.append(f"      {one_line(label)!r}: {inner.name}({inner_rendered})  # {inner.tool}")
    return "\n".join(lines)


def render_choice(choice: dict[str, object]) -> str:
    """Render the epilogue verdict: which labels matched, and what every alternative saw."""
    matched = [str(label) for label in choice.get("matched", [])]
    evaluated = choice.get("evaluated", [])
    headline = (
        "CHOICE → matched: " + ", ".join(repr(one_line(label)) for label in matched)
        if matched
        else "CHOICE → no alternative matched"
    )
    lines = [headline]
    for record in evaluated:
        lines.append(
            f"  {record['status']:<9} {one_line(record['label'])!r:<28} "
            f"{record['step']}({record['tool']}) {record['elapsed_ms']} ms"
        )
    return "\n".join(lines)


def render_execution(
    *,
    action: str,
    session_id: str,
    attached: bool,
    session_alive: bool,
    results: list[dict[str, object]],
    rescues: list[str],
    errors: int,
) -> str:
    """Render an execution (or dry run) as a markdown transcript."""
    lines = [
        f"Scenario {action} — session '{one_line(session_id)}' ({'attached' if attached else 'launched'})",
        "=" * 60,
    ]
    for record in results:
        lines.extend(
            [
                f"[{record['n']:>2}] {record['step']}({record['tool']}) — {record['status']} ({record['elapsed_ms']} ms)",
                str(record["output"]).rstrip(),
                "",
            ]
        )
        if record.get("choice"):
            lines.extend([render_choice(dict(record["choice"])), ""])
    lines.extend(
        [
            "-" * 60,
            f"steps: {len(results)} · errored: {errors} · session {'left open' if session_alive else 'closed'}",
        ]
    )
    if rescues:
        lines.append("rescue files created during the run (teardown debris — delete them): " + ", ".join(rescues))
    return "\n".join(lines)


_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _path_like(token: str) -> bool:
    """True when a shell token plausibly names a file or directory."""
    if not token or token.startswith("-"):
        return False
    if _ENV_ASSIGNMENT.match(token):  # `TERM=xterm-256color` is not a path
        return False
    tail = token.rstrip("/").rsplit("/", 1)[-1]
    return "/" in token or "." in tail


def rescue_dirs(commands: list[str], extra_dirs: set[Path]) -> set[Path]:
    """Directories a scenario could leave rescue files in.

    Every shell token that looks like a path is resolved (relative tokens against the current
    directory) and its directory is watched. A leaf that does not exist yet still contributes its
    parent: the editor creates the rescue file there, and the child may create the fixture itself.
    """
    dirs = {directory for directory in extra_dirs if directory.is_dir()}
    for command in commands:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for token in tokens:
            if not _path_like(token):
                continue
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            directory = candidate if candidate.is_dir() else candidate.parent
            if directory.is_dir():
                dirs.add(directory)
    return dirs


def save_files(dirs: set[Path]) -> set[str]:
    """Snapshot of the ``*.save`` files in these directories."""
    found: set[str] = set()
    for directory in dirs:
        try:
            found.update(str(path) for path in directory.glob("*.save"))
        except OSError:
            continue
    return found


def new_save_files(dirs: set[Path], before: set[str]) -> list[str]:
    """Rescue files that appeared while the scenario ran.

    ``close_session`` SIGHUPs the child, so an editor with a modified buffer leaves a rescue copy next
    to the file it was editing — teardown debris, not scenario evidence. This compares snapshots
    rather than file mtimes: a filesystem timestamp can sit a tick behind ``time.time()``, which made
    an mtime comparison miss files that had just been written.
    """
    return sorted(save_files(dirs) - before)

"""Unit tests for _scenario.py: script parsing, lint diagnostics, pipeline resolution.

Everything here is pure — no subprocess, no session, no filesystem beyond tmp_path.
"""

from mcp_tui_test._scenario import (
    STEP_SPECS,
    new_save_files,
    parse_document,
    parse_script,
    render_choice,
    render_diagnostics,
    render_execution,
    render_pipeline,
    rescue_dirs,
    save_files,
)

STREAM_SCRIPT = """\
launch("python app.py", mode="stream", dimensions="100x30", timeout=15)
expect("Please choose", timeout=5)
send("3\\r")
assert_text("Counter value: 1")
close()
"""


def codes(plan):
    return [diagnostic.code for diagnostic in plan.diagnostics]


def messages(plan):
    """Every message plus hint — hints are what make a lint report actionable."""
    return "\n".join(f"{diagnostic.message}\n{diagnostic.hint}" for diagnostic in plan.diagnostics)


class TestParsing:
    def test_valid_script_resolves_pipeline(self):
        plan = parse_script(STREAM_SCRIPT)
        assert plan.ok, messages(plan)
        assert [step.tool for step in plan.steps] == [
            "launch_tui",
            "expect_text",
            "send_keys",
            "assert_contains",
            "close_session",
        ]
        assert plan.steps[0].arguments == {
            "command": "python app.py",
            "mode": "stream",
            "dimensions": "100x30",
            "timeout": 15,
        }
        assert plan.launches and plan.closes and plan.stream_mode

    def test_literal_dict_argument_resolves(self):
        plan = parse_script('launch("app", env={"TERM": "xterm-256color"})\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["env"] == {"TERM": "xterm-256color"}

    def test_variadic_positional_keys(self):
        plan = parse_script('key("down", "down", "enter")\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["keys"] == ["down", "down", "enter"]

    def test_variadic_keyword_keys_list(self):
        plan = parse_script('key(keys=["up", "enter"])\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["keys"] == ["up", "enter"]

    def test_empty_script_is_an_error(self):
        assert "E_EMPTY_SCRIPT" in codes(parse_script(""))

    def test_syntax_error_reports_position(self):
        plan = parse_script("launch('unterminated\n")
        (diagnostic,) = plan.diagnostics
        assert diagnostic.code == "E_SYNTAX"
        assert diagnostic.line == 1
        assert "does not parse" in diagnostic.message

    def test_pipeline_payload_is_json_friendly(self):
        payload = parse_script('expect("hi", timeout=3)').payload()
        assert payload == [{"step": "expect", "tool": "expect_text", "arguments": {"pattern": "hi", "timeout": 3}, "line": 1}]


class TestArgumentValidation:
    def test_unknown_step_suggests_a_step(self):
        plan = parse_script('send_keyz("x")')
        assert "E_UNKNOWN_STEP" in codes(plan)
        assert "did you mean 'send'" in messages(plan)
        assert "the tool is send_keys" in messages(plan)

    def test_unknown_step_without_close_suggestion(self):
        plan = parse_script("blorp(1)")
        assert "valid steps:" in messages(plan)

    def test_unknown_keyword_suggests_parameter(self):
        plan = parse_script('expect("x", timeouts=3)')
        assert "E_ARG_UNKNOWN" in codes(plan)
        assert "did you mean 'timeout'" in messages(plan)

    def test_missing_required_argument_shows_usage(self):
        plan = parse_script("send()")
        assert "E_ARG_MISSING" in codes(plan)
        assert "send(keys, delay=0.3" in messages(plan)

    def test_variadic_needs_at_least_one_value(self):
        plan = parse_script("key()")
        assert "E_ARG_MISSING" in codes(plan)
        assert "needs at least one keys" in messages(plan)

    def test_wrong_type(self):
        plan = parse_script('line("one")')
        assert "E_ARG_TYPE" in codes(plan)
        assert "expects int, got str" in messages(plan)

    def test_bool_is_not_accepted_as_int(self):
        plan = parse_script("line(True)")
        assert "E_ARG_TYPE" in codes(plan)

    def test_int_is_accepted_for_float_parameter(self):
        plan = parse_script('send("x", delay=1)')
        assert plan.ok, messages(plan)

    def test_duplicate_argument(self):
        plan = parse_script('send("x", keys="y")')
        assert "E_ARG_DUPLICATE" in codes(plan)

    def test_too_many_positional_arguments(self):
        plan = parse_script('line(1, 2)')
        assert "E_ARG_TOO_MANY_POSITIONAL" in codes(plan)

    def test_non_literal_arguments_are_rejected(self):
        for source in ('send(keys)', 'send(f"{x}")', 'send(str(1))', 'line(row.value)', 'campaign'):
            plan = parse_script(source)
            assert plan.errors, source
            assert "E_ARG_NOT_LITERAL" in codes(plan) or "E_UNKNOWN_STEP" in codes(plan) or "E_NOT_A_STEP_CALL" in codes(plan)

    def test_starred_and_kwargs_splat_are_rejected(self):
        assert "E_ARG_STARRED" in codes(parse_script("key(*names)"))
        assert "E_ARG_KW_STAR" in codes(parse_script("send(**options)"))

    def test_session_id_is_rejected_inside_a_script(self):
        plan = parse_script('send("x", session_id="other")')
        assert "E_SESSION_ARG" in codes(plan)
        assert "pass session_id to run_scenario()" in messages(plan)


class TestStructure:
    def test_assignment_is_not_a_step_call(self):
        plan = parse_script("count = 1")
        assert "E_NOT_A_STEP_CALL" in codes(plan)

    def test_import_is_not_a_step_call(self):
        plan = parse_script("import os")
        assert "E_NOT_A_STEP_CALL" in codes(plan)

    def test_control_flow_is_not_allowed(self):
        plan = parse_script('if True:\n    send("x")\n')
        assert "E_NOT_A_STEP_CALL" in codes(plan)

    def test_bare_value_is_not_a_step_call(self):
        plan = parse_script('"hello"')
        assert "E_NOT_A_STEP_CALL" in codes(plan)

    def test_attribute_call_is_rejected(self):
        plan = parse_script("nano.start()")
        assert "E_ATTR_CALL" in codes(plan)
        assert "only bare step calls" in messages(plan)

    def test_steps_after_close_are_unreachable(self):
        plan = parse_script('launch("app")\nclose()\ncapture()')
        assert "E_UNREACHABLE" in codes(plan)
        assert [step.tool for step in plan.steps] == ["launch_tui", "close_session"]


class TestSemantics:
    def test_invalid_regex_is_caught_at_lint_time(self):
        plan = parse_script('expect("[unclosed")')
        assert "E_REGEX_INVALID" in codes(plan)
        assert "escape regex metacharacters" in messages(plan)

    def test_valid_regex_passes(self):
        assert parse_script(r'expect(r"\d+ items")').ok

    def test_unknown_special_key_lists_valid_names(self):
        plan = parse_script('key("f99")')
        assert "E_SPECIAL_KEY" in codes(plan)
        assert "page_down" in messages(plan)

    def test_ctrl_requires_a_single_letter(self):
        assert "E_CTRL_KEY" in codes(parse_script('ctrl("enter")'))
        assert parse_script('ctrl("o")').ok

    def test_wrong_type_does_not_also_report_the_argument_as_missing(self):
        assert codes(parse_script("ctrl(1)")) == ["E_ARG_TYPE"]
        assert codes(parse_script('line("one")')) == ["E_ARG_TYPE"]

    def test_newline_escape_warns_for_buffer_launches(self):
        plan = parse_script('launch("app")\nsend("q\\n")\nclose()')
        assert plan.ok
        assert "W_ENTER_ESCAPE" in codes(plan)

    def test_newline_escape_is_fine_for_line_oriented_apps(self):
        # A stream-mode CLI reads lines, so "\n" is the correct Enter there.
        assert "W_ENTER_ESCAPE" not in codes(parse_script('launch("app", mode="stream")\nsend("q\\n")'))
        assert "W_ENTER_ESCAPE" not in codes(parse_script('send("q\\n")'))

    def test_negative_screen_coordinates_are_errors(self):
        plan = parse_script('line(-1)')
        assert "E_NEGATIVE_POSITION" in codes(plan)

    def test_buffer_only_read_after_stream_launch_warns(self):
        plan = parse_script('launch("app", mode="stream")\nline(1)')
        assert "W_STREAM_MODE_READ" in codes(plan)
        assert plan.ok

    def test_buffer_read_after_buffer_launch_does_not_warn(self):
        plan = parse_script('launch("app", mode="buffer")\ncursor()\nclose()')
        assert "W_STREAM_MODE_READ" not in codes(plan)

    def test_framed_change_reference_warns(self):
        plan = parse_script(f'change("Screen output\\n{"=" * 60}\\nhello")')
        assert "W_CHANGE_REFERENCE_FRAMED" in codes(plan)

    def test_missing_launch_and_close_warn(self):
        plan = parse_script('expect("hi")')
        assert set(codes(plan)) == {"W_NO_LAUNCH", "W_NO_CLOSE"}

    def test_launch_not_first_warns_about_replacement(self):
        plan = parse_script('expect("hi")\nlaunch("app")\nclose()')
        assert "W_LAUNCH_NOT_FIRST" in codes(plan)
        assert "W_LAUNCH_REPLACES_SESSION" in codes(plan)

    def test_special_keys_are_injectable(self):
        plan = parse_script('key("down")', special_keys=frozenset({"enter"}))
        assert "E_SPECIAL_KEY" in codes(plan)

    def test_every_step_maps_to_a_real_tool(self):
        tools = {spec.tool for spec in STEP_SPECS.values()}
        assert len(tools) == 21
        assert {"launch_tui", "close_session", "screenshot", "gif", "resize", "get_cell_style"} <= tools


class TestRendering:
    def test_diagnostics_render_with_caret_and_hint(self):
        source = 'expect("x", timeouts=3)'
        plan = parse_script(source)
        report = render_diagnostics(plan.diagnostics, source)
        assert "1:13: error: E_ARG_UNKNOWN: expect() has no argument 'timeouts'" in report
        assert "^" in report
        assert "hint: did you mean 'timeout'?" in report

    def test_pipeline_renders_canonical_calls(self):
        rendered = render_pipeline(parse_script('launch("app", mode="buffer")\nkey("down", "enter")').steps)
        assert '1. launch(command=\'app\', mode=\'buffer\')  # launch_tui' in rendered
        assert "2. key(keys=['down', 'enter'])  # send_special_keys" in rendered

    def test_execution_renderer_reports_summary_and_debris(self):
        report = render_execution(
            action="executed",
            session_id="s",
            attached=False,
            session_alive=False,
            results=[{"n": 1, "step": "launch", "tool": "launch_tui", "status": "ok", "elapsed_ms": 12, "output": "✓"}],
            rescues=["/tmp/notes.txt.save"],
            errors=0,
        )
        assert "Scenario executed — session 's' (launched)" in report
        assert "[ 1] launch(launch_tui) — ok (12 ms)" in report
        assert "steps: 1 · errored: 0 · session closed" in report
        assert "rescue files created during the run" in report

    def test_execution_renderer_omits_debris_line_when_clean(self):
        report = render_execution(
            action="executed",
            session_id="s",
            attached=True,
            session_alive=True,
            results=[],
            rescues=[],
            errors=0,
        )
        assert "rescue files" not in report
        assert "session left open" in report


class TestRescueFiles:
    def test_reports_only_files_that_appeared_during_the_run(self, tmp_path):
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        notes = fixture_dir / "notes.txt"
        notes.write_text("line one\n")
        stale = fixture_dir / "old.txt.save"
        stale.write_text("from a previous run")

        dirs = rescue_dirs([f"env TERM=xterm nano {notes}"], set())
        before = save_files(dirs)
        assert before == {str(stale)}

        fresh = fixture_dir / "notes.txt.save"
        fresh.write_text("rescue")
        assert new_save_files(dirs, before) == [str(fresh)]

    def test_no_new_files_means_no_debris(self, tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("x")
        dirs = rescue_dirs([f"nano {notes}"], set())
        assert new_save_files(dirs, save_files(dirs)) == []

    def test_unknown_paths_contribute_no_directories(self):
        assert rescue_dirs(["env nano /does/not/exist.txt"], set()) == set()


class TestReviewFindings:
    """Regressions for defects found by the adversarial review."""

    def test_invalid_mode_is_rejected(self):
        plan = parse_script('launch("cat", mode="BOGUS")\nline(0)\nclose()')
        assert "E_ARG_VALUE" in codes(plan)
        assert "valid modes" in messages(plan)
        # The linter used to assume buffer mode for anything that was not "stream".
        assert "W_STREAM_MODE_READ" not in codes(plan)

    def test_out_of_range_dimensions_are_rejected(self):
        for size in ("200000x200000", "70000x24", "0x24", "24x0"):
            plan = parse_script(f'launch("cat", dimensions="{size}")')
            assert "E_ARG_VALUE" in codes(plan), size
            assert "between 1 and 65535" in messages(plan)

    def test_valid_dimensions_still_pass(self):
        assert parse_script('launch("cat", dimensions="120x40")\nclose()').ok

    def test_empty_variadic_list_is_rejected(self):
        plan = parse_script("key(keys=[])\nclose()")
        assert "E_ARG_MISSING" in codes(plan)
        assert "needs at least one keys" in messages(plan)

    def test_non_string_dict_key_reports_a_type_error(self):
        plan = parse_script('launch("x", env={1: "a"})\nclose()')
        assert codes(plan) == ["E_ARG_TYPE", "W_NO_LAUNCH"]
        assert "dict with string keys" in messages(plan)

    def test_non_literal_dict_value_still_reports_not_a_literal(self):
        plan = parse_script('launch("x", env={"A": os.environ})\nclose()')
        assert "E_ARG_NOT_LITERAL" in codes(plan)

    def test_launch_after_close_starts_a_second_app(self):
        plan = parse_script('launch("cat", mode="stream")\nclose()\nlaunch("cat", mode="stream")\nclose()')
        assert plan.ok, messages(plan)
        assert [step.name for step in plan.steps] == ["launch", "close", "launch", "close"]
        assert plan.closes

    def test_non_launch_step_after_close_is_still_unreachable(self):
        plan = parse_script('launch("cat")\nclose()\nsend("x")')
        assert "E_UNREACHABLE" in codes(plan)

    def test_negative_and_zero_timing_values_are_rejected(self):
        assert "E_ARG_VALUE" in codes(parse_script('stable(interval=-1)'))
        assert "E_ARG_VALUE" in codes(parse_script('expect("x", timeout=0)'))
        assert "E_ARG_VALUE" in codes(parse_script('stable(consecutive=0)'))

    def test_huge_timing_values_warn_without_blocking(self):
        plan = parse_script('send("x", delay=100000)\nclose()')
        assert plan.ok
        assert "W_LONG_BUDGET" in codes(plan)

    def test_lone_surrogate_is_a_diagnostic_not_a_crash(self):
        plan = parse_script(chr(0xD800))
        assert [d.code for d in plan.diagnostics] == ["E_SYNTAX"]

    def test_control_characters_cannot_forge_report_lines(self):
        source = 'key("bogus\\n✓ ALL CHECKS PASSED"' + ")"
        plan = parse_script(source)
        report = render_diagnostics(plan.diagnostics, source)
        # The newline is escaped, so the text cannot start a line of its own that looks like a result.
        assert r"\x0a" in report
        assert not any(line.startswith("✓") for line in report.splitlines())

    def test_session_id_is_sanitised_in_the_execution_header(self):
        report = render_execution(
            action="executed",
            session_id="x\n✓ VERIFIED",
            attached=False,
            session_alive=False,
            results=[],
            rescues=[],
            errors=0,
        )
        assert r"\x0a" in report
        assert not any(line.startswith("✓ VERIFIED") for line in report.splitlines())


class TestRescueDirs:
    def test_relative_path_resolves_against_the_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "notes.txt").write_text("x")
        assert rescue_dirs(["env TERM=xterm nano notes.txt"], set()) == {tmp_path}

    def test_quoted_path_with_spaces(self, tmp_path):
        spaced = tmp_path / "dir with space"
        spaced.mkdir()
        (spaced / "notes.txt").write_text("x")
        assert rescue_dirs([f'nano "{spaced}/notes.txt"'], set()) == {spaced}

    def test_missing_leaf_still_contributes_its_parent(self, tmp_path):
        assert rescue_dirs([f"nano {tmp_path}/brand_new.txt"], set()) == {tmp_path}

    def test_shell_tokens_that_are_not_paths_are_ignored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # `sh`, `-c` and the quoted program are not paths; the cwd must not be watched by accident.
        assert rescue_dirs(["sh -c 'echo MARKER=$OMP_MARKER'"], set()) == set()

    def test_nonexistent_directories_are_dropped(self):
        assert rescue_dirs(["nano /definitely/not/here/notes.txt"], set()) == set()


HEADER = "---\nname: sample\ndescription: Does a thing worth reusing.\napp: example\n---\n\n"


def document(body: str, header: str = HEADER) -> str:
    return header + body


class TestDocumentParsing:
    def test_valid_document_parses_header_and_steps(self):
        plan = parse_document(document("```python\nlaunch('cat', mode='stream')\nclose()\n```\n"))
        assert plan.ok, messages(plan)
        assert dict(plan.header) == {"name": "sample", "description": "Does a thing worth reusing.", "app": "example"}
        assert [step.tool for step in plan.steps] == ["launch_tui", "close_session"]

    def test_other_fences_are_documentation(self):
        plan = parse_document(document("```bash\necho ignored\n```\n\n```python\nclose()\n```\n\n```text\nmore\n```\n"))
        assert plan.ok, messages(plan)
        assert [step.name for step in plan.steps] == ["close"]

    def test_document_without_header_is_an_error(self):
        plan = parse_document("prose\n\n```python\nclose()\n```\n")
        assert "E_MISSING_HEADER" in codes(plan)
        assert [step.name for step in plan.steps] == ["close"]  # still resolves the pipeline

    def test_header_needs_name_and_description(self):
        plan = parse_document(document("```python\nclose()\n```\n", header="---\napp: only\n---\n\n"))
        assert codes(plan).count("E_HEADER_FIELD") == 2
        assert "header is missing 'name'" in messages(plan)

    def test_unterminated_header(self):
        plan = parse_document("---\nname: x\n\n```python\nclose()\n```\n")
        assert "E_HEADER_SYNTAX" in codes(plan)

    def test_unreadable_header_line(self):
        plan = parse_document(document("```python\nclose()\n```\n", header="---\nname: x\ndescription: y\nnot a pair\n---\n\n"))
        assert "E_HEADER_SYNTAX" in codes(plan)

    def test_header_comments_and_quotes(self):
        plan = parse_document(
            document("```python\nclose()\n```\n", header='---\n# a comment\nname: "quoted name"\ndescription: y\n---\n\n')
        )
        assert dict(plan.header)["name"] == "quoted name"

    def test_requires_exactly_one_python_block(self):
        none = parse_document(document("just prose\n"))
        assert [d.code for d in none.diagnostics] == ["E_NO_PYTHON_BLOCK"]
        twice = parse_document(document("```python\nclose()\n```\n\n```python\nclose()\n```\n"))
        assert "E_MULTIPLE_PYTHON_BLOCKS" in codes(twice)
        assert "python blocks start at lines" in messages(twice)

    def test_unterminated_code_block(self):
        plan = parse_document(document("```python\nclose()\n"))
        assert "E_UNTERMINATED_BLOCK" in codes(plan)

    def test_pipeline_lines_match_the_document_not_the_block(self):
        source = document("prose\n\n```python\nlaunch('cat', mode='stream')\nchoice({\"a\": assert_text('x')})\n```\n")
        plan = parse_document(source)
        assert plan.ok, messages(plan)
        lines = source.splitlines()
        assert plan.steps[0].line == lines.index("launch('cat', mode='stream')") + 1
        assert plan.steps[1].line == lines.index('choice({"a": assert_text(\'x\')})') + 1
        assert [entry["line"] for entry in plan.payload()] == [plan.steps[0].line, plan.steps[1].line]

    def test_block_diagnostics_point_at_document_lines(self):
        source = document("prose line\nmore prose\n\n```python\nlaunch('cat', mode='stream')\nsend_keyz('x')\n```\n")
        plan = parse_document(source)
        (diagnostic,) = [d for d in plan.diagnostics if d.code == "E_UNKNOWN_STEP"]
        expected_line = source.splitlines().index("send_keyz('x')") + 1
        assert diagnostic.line == expected_line
        report = render_diagnostics([diagnostic], source)
        assert "send_keyz('x')" in report
        assert report.startswith(f"{expected_line}:1: error: E_UNKNOWN_STEP")


class TestChoiceEpilogue:
    def test_choice_resolves_alternatives_in_order(self):
        plan = parse_script(
            "launch('cat', mode='buffer')\n"
            'choice({"a": expect("x", timeout=2), "b": assert_text("y"), "c": assert_at("z", 1, 0)})\n'
        )
        assert plan.ok, messages(plan)
        choice = plan.steps[-1]
        assert choice.name == "choice" and choice.tool == "choice"
        assert [label for label, _ in choice.alternatives] == ["a", "b", "c"]
        assert [inner.tool for _, inner in choice.alternatives] == ["expect_text", "assert_contains", "assert_at_position"]
        assert plan.payload()[-1]["alternatives"][0]["label"] == "a"

    def test_nothing_may_follow_the_epilogue(self):
        plan = parse_script("launch('cat', mode='stream')\nchoice({\"a\": assert_text('x')})\nclose()\n")
        assert "E_AFTER_CHOICE" in codes(plan)
        assert [step.name for step in plan.steps] == ["launch", "choice"]

    def test_epilogue_cannot_follow_close(self):
        # close() kills the session, so an epilogue after it could only evaluate against a corpse.
        plan = parse_script('launch("cat", mode="stream")\nclose()\nchoice({"a": assert_text("x")})')
        assert "E_UNREACHABLE" in codes(plan)
        assert [step.name for step in plan.steps] == ["launch", "close"]

    def test_epilogue_is_allowed_after_a_relaunch(self):
        plan = parse_script('launch("cat", mode="stream")\nclose()\nlaunch("cat", mode="stream")\nchoice({"a": assert_text("x")})')
        assert plan.ok, messages(plan)
        assert [step.name for step in plan.steps] == ["launch", "close", "launch", "choice"]

    def test_only_one_epilogue(self):
        plan = parse_script("choice({\"a\": assert_text('x')})\nchoice({\"b\": assert_text('y')})\n")
        assert "E_MULTIPLE_CHOICE" in codes(plan)

    def test_only_assertions_are_allowed_inside(self):
        for body in ('capture()', 'close()', 'line(row=0)', 'send("x")'):
            plan = parse_script(f'choice({{"a": {body}}})')
            assert "E_CHOICE_INSTRUCTION" in codes(plan), body
        nested = parse_script('choice({"a": choice({"b": assert_text("x")})})')
        assert "E_CHOICE_INSTRUCTION" in codes(nested)

    def test_labels_must_be_unique_non_empty_strings(self):
        assert "E_CHOICE_LABEL" in codes(parse_script('choice({"a": assert_text("x"), "a": assert_text("y")})'))
        assert "E_CHOICE_LABEL" in codes(parse_script('choice({"": assert_text("x")})'))
        assert "E_CHOICE_LABEL" in codes(parse_script('choice({1: assert_text("x")})'))

    def test_choice_needs_a_dict_of_instructions(self):
        assert "E_CHOICE_ARGUMENT" in codes(parse_script("choice()"))
        assert "E_CHOICE_ARGUMENT" in codes(parse_script("choice(['a'])"))
        assert "E_CHOICE_EMPTY" in codes(parse_script("choice({})"))

    def test_alternative_arguments_are_validated_like_any_step(self):
        plan = parse_script('choice({"a": expect("x", timeouts=2)})')
        assert "E_ARG_UNKNOWN" in codes(plan)
        plan = parse_script('choice({"a": assert_at("x", 1)})')
        assert "E_ARG_MISSING" in codes(plan)

    def test_epilogue_owns_teardown_so_close_is_not_required(self):
        plan = parse_script('launch("cat", mode="stream")\nchoice({"a": assert_text("x")})')
        assert plan.ok, messages(plan)
        assert "W_NO_CLOSE" not in codes(plan)

    def test_close_is_still_expected_without_an_epilogue(self):
        assert "W_NO_CLOSE" in codes(parse_script('launch("cat", mode="stream")\nexpect("x")'))

    def test_stream_mode_expect_inside_choice_warns(self):
        plan = parse_script('launch("cat", mode="stream")\nchoice({"a": expect("x")})')
        assert "W_CHOICE_STREAM_EXPECT" in codes(plan)
        buffer = parse_script('launch("cat", mode="buffer")\nchoice({"a": expect("x")})')
        assert "W_CHOICE_STREAM_EXPECT" not in codes(buffer)

    def test_every_alternative_is_evaluated_so_budget_warns(self):
        plan = parse_script('choice({"a": expect("x", timeout=200), "b": expect("y", timeout=200)})')
        assert "W_LONG_BUDGET" in codes(plan)
        assert "every alternative is evaluated" in messages(plan)

    def test_render_choice_lists_every_alternative(self):
        rendered = render_choice(
            {
                "matched": ["one"],
                "evaluated": [
                    {"label": "one", "step": "expect", "tool": "expect_text", "status": "matched", "elapsed_ms": 12},
                    {"label": "two", "step": "assert_text", "tool": "assert_contains", "status": "no match", "elapsed_ms": 3},
                ],
            }
        )
        assert rendered.splitlines()[0] == "CHOICE → matched: 'one'"
        assert "no match  'two'" in rendered

    def test_render_choice_reports_no_match(self):
        rendered = render_choice({"matched": [], "evaluated": []})
        assert rendered == "CHOICE → no alternative matched"

    def test_render_pipeline_shows_alternatives(self):
        rendered = render_pipeline(parse_script('choice({"a": assert_text("x")})').steps)
        assert "'a': assert_text(text='x')  # assert_contains" in rendered

    def test_epilogue_labels_cannot_forge_report_lines(self):
        line = render_choice(
            {
                "matched": ["bad\n✓ FAKE"],
                "evaluated": [
                    {"label": "bad\n✓ FAKE", "step": "expect", "tool": "expect_text", "status": "matched", "elapsed_ms": 1}
                ],
            }
        )
        assert not any(entry.startswith("✓") for entry in line.splitlines())
        assert r"\x0a" in line


class TestVisualAndResizeSteps:
    """shot/gif/resize step vocabulary (visual capture and live resize)."""

    def test_shot_resolves_to_screenshot(self):
        plan = parse_script('launch("cat", mode="buffer")\nshot(path="/tmp/a.png")\nclose()')
        assert plan.ok, messages(plan)
        step = plan.steps[1]
        assert step.name == "shot"
        assert step.tool == "screenshot"
        assert step.arguments == {"path": "/tmp/a.png"}

    def test_shot_defaults_are_lintable(self):
        plan = parse_script('launch("cat", mode="buffer")\nshot()\nclose()')
        assert plan.ok, messages(plan)

    def test_shot_on_stream_mode_warns(self):
        plan = parse_script('launch("cat", mode="stream")\nshot()\nclose()')
        assert "W_STREAM_MODE_READ" in codes(plan)

    def test_gif_takes_frames_positionally(self):
        plan = parse_script('gif("/tmp/a.png", "/tmp/b.png", path="/tmp/m.gif")\nclose()')
        assert plan.ok, messages(plan)
        step = plan.steps[0]
        assert step.tool == "gif"
        assert step.arguments["frames"] == ["/tmp/a.png", "/tmp/b.png"]
        assert step.arguments["path"] == "/tmp/m.gif"

    def test_gif_accepts_a_frames_keyword(self):
        plan = parse_script('gif(frames=["/tmp/a.png", "/tmp/b.png"])\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["frames"] == ["/tmp/a.png", "/tmp/b.png"]

    def test_gif_accepts_a_frames_list_positionally(self):
        """A list literal is accepted positionally, as it is by keyword."""
        plan = parse_script('gif(["/tmp/a.png", "/tmp/b.png"], path="/tmp/m.gif")\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["frames"] == ["/tmp/a.png", "/tmp/b.png"]

    def test_empty_list_variadic_is_rejected(self):
        plan = parse_script("gif([])\nclose()")
        assert "E_ARG_MISSING" in codes(plan)

    def test_key_accepts_a_list_positionally(self):
        plan = parse_script('key(["down", "enter"])\nclose()')
        assert plan.ok, messages(plan)
        assert plan.steps[0].arguments["keys"] == ["down", "enter"]

    def test_gif_frames_is_required(self):
        plan = parse_script("gif()\nclose()")
        assert "E_ARG_MISSING" in codes(plan)

    def test_gif_frames_must_be_strings(self):
        plan = parse_script("gif(1, 2)\nclose()")
        assert "E_ARG_TYPE" in codes(plan)

    def test_resize_resolves_to_the_resize_tool(self):
        plan = parse_script('launch("cat", mode="buffer")\nresize("80x20")\nline(0)\nclose()')
        assert plan.ok, messages(plan)
        step = plan.steps[1]
        assert step.name == "resize"
        assert step.tool == "resize"
        assert step.arguments == {"dimensions": "80x20"}

    def test_resize_requires_dimensions(self):
        plan = parse_script("resize()\nclose()")
        assert "E_ARG_MISSING" in codes(plan)

    def test_resize_rejects_a_session_argument(self):
        plan = parse_script('resize("80x20", session_id="x")\nclose()')
        assert "E_SESSION_ARG" in codes(plan)


class TestStyleStep:
    """The `style` step exposes per-cell colour/attributes to a scenario."""

    def test_style_resolves_to_get_cell_style(self):
        plan = parse_script('launch("cat", mode="buffer")\nstyle(3, 7)\nclose()')
        assert plan.ok, messages(plan)
        step = plan.steps[1]
        assert step.name == "style"
        assert step.tool == "get_cell_style"
        assert step.arguments == {"row": 3, "col": 7}

    def test_style_requires_both_coordinates(self):
        plan = parse_script("style(1)\nclose()")
        assert "E_ARG_MISSING" in codes(plan)

    def test_style_on_stream_mode_warns(self):
        plan = parse_script('launch("cat", mode="stream")\nstyle(0, 0)\nclose()')
        assert "W_STREAM_MODE_READ" in codes(plan)

    def test_style_rejects_negative_coordinates(self):
        plan = parse_script('style(-1, 0)\nclose()')
        assert "E_NEGATIVE_POSITION" in codes(plan)

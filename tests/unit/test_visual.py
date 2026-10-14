"""Unit tests for the visual tools (screenshot, gif) and their renderer.

Pillow is a declared dependency, so these run for real: the point of the tools is
that a colour-only screen can be *looked at*, and a renderer tested only against
a mock would not prove that.
"""

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from mcp_tui_test._render import frames_to_gif, render_screen_png, resolve_color
from mcp_tui_test._server import ScreenSession, gif, get_cell_style, resize, screenshot
from tests.conftest import DummyProcess

RED = (205, 0, 0)
DEFAULT_FG = (229, 229, 229)
DEFAULT_BG = (0, 0, 0)


def _buffer_session(columns: int = 20, lines: int = 3, output: str = "") -> ScreenSession:
    session = ScreenSession(
        process=DummyProcess(),
        dimensions=(columns, lines),
        mode="buffer",
        session_id="visual",
    )
    if output:
        session.stream.feed(output)
    return session


class TestResolveColor:
    def test_named_colour(self):
        assert resolve_color("red", DEFAULT_FG) == RED

    def test_bright_colour(self):
        assert resolve_color("brightgreen", DEFAULT_FG) == (0, 255, 0)

    def test_hex_colour(self):
        assert resolve_color("ff8700", DEFAULT_FG) == (255, 135, 0)

    def test_default_is_fallback(self):
        assert resolve_color("default", DEFAULT_FG) == DEFAULT_FG
        assert resolve_color(None, DEFAULT_FG) == DEFAULT_FG

    def test_unknown_name_falls_back(self):
        assert resolve_color("chartreuse", DEFAULT_FG) == DEFAULT_FG


class TestRenderScreenPng:
    def test_writes_a_png(self, tmp_path):
        from PIL import Image

        session = _buffer_session()
        target = tmp_path / "frame.png"
        written = render_screen_png(session.screen, target)
        assert written == str(target)
        assert target.is_file()
        assert Image.open(target).size[0] > 0

    def test_draws_coloured_text(self, tmp_path):
        """A red glyph on black must leave a non-default pixel: the render is not a stub."""
        from PIL import Image

        session = _buffer_session(output="\x1b[31mXX")
        target = tmp_path / "red.png"
        render_screen_png(session.screen, target)
        pixels = Image.open(target).convert("RGB").getcolors(maxcolors=1 << 16)
        assert any(colour not in (DEFAULT_BG, (0, 0, 0)) for _, colour in pixels)

    def test_paints_the_cursor(self, tmp_path):
        """The cursor block must be visible — it is the only witness of focus."""
        from PIL import Image

        session = _buffer_session()
        session.screen.cursor.x = 3
        session.screen.cursor.y = 1
        cursor_on = tmp_path / "cursor.png"
        cursor_off = tmp_path / "no-cursor.png"
        render_screen_png(session.screen, cursor_on, show_cursor=True)
        render_screen_png(session.screen, cursor_off, show_cursor=False)
        assert cursor_on.read_bytes() != cursor_off.read_bytes()
        assert DEFAULT_FG in [
            colour for _, colour in Image.open(cursor_on).convert("RGB").getcolors(maxcolors=1 << 16)
        ]

    def test_wide_glyph_does_not_shift_columns(self, tmp_path):
        """A wide glyph advances two canvas columns, so the frame keeps its size."""
        from PIL import Image

        session = _buffer_session(columns=10, output="a\U0001F3F4b")
        target = tmp_path / "wide.png"
        render_screen_png(session.screen, target, font_size=10)
        width = Image.open(target).size[0]
        plain = _buffer_session(columns=10)
        plain_path = tmp_path / "plain.png"
        render_screen_png(plain.screen, plain_path, font_size=10)
        assert width == Image.open(plain_path).size[0]


class TestFramesToGif:
    def test_stitches_frames(self, tmp_path):
        from PIL import Image

        frame_a = tmp_path / "a.png"
        frame_b = tmp_path / "b.png"
        first = _buffer_session(output="one")
        second = _buffer_session(output="two")
        render_screen_png(first.screen, frame_a)
        render_screen_png(second.screen, frame_b)
        out = tmp_path / "movie.gif"
        written = frames_to_gif([str(frame_a), str(frame_b)], out, duration_ms=100)
        assert written == str(out)
        assert Image.open(out).n_frames == 2

    def test_empty_frame_list_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            frames_to_gif([], tmp_path / "empty.gif")


class TestScreenshotTool:
    def test_stream_mode_rejected(self, fresh_sessions, tmp_path):
        fresh_sessions["s"] = ScreenSession(
            process=DummyProcess(), dimensions=(80, 24), mode="stream", session_id="s"
        )
        with pytest.raises(ToolError, match="buffer mode"):
            screenshot(path=str(tmp_path / "x.png"), session_id="s")

    def test_bad_font_size_rejected(self, fresh_sessions, tmp_path):
        fresh_sessions["s"] = _buffer_session()
        with pytest.raises(ToolError, match="font_size"):
            screenshot(path=str(tmp_path / "x.png"), session_id="s", font_size=200)

    def test_writes_png_and_reports_path(self, fresh_sessions, tmp_path):
        fresh_sessions["s"] = _buffer_session(output="hello")
        target = tmp_path / "shot.png"
        result = screenshot(path=str(target), session_id="s")
        assert "✓ Screenshot saved" in result
        assert str(target.resolve()) in result
        assert target.is_file()

    def test_dead_child_rejected(self, fresh_sessions, tmp_path):
        fresh_sessions["s"] = ScreenSession(
            process=DummyProcess(alive=False), dimensions=(20, 3), mode="buffer", session_id="s"
        )
        with pytest.raises(ToolError):
            screenshot(path=str(tmp_path / "x.png"), session_id="s")


def _symbol_fonts_available() -> bool:
    """Coverage testing needs a font that can draw the glyph being tested."""
    from mcp_tui_test._render import _FALLBACK_FONT_CANDIDATES, primary_font_path

    return bool(primary_font_path()) or any(Path(p).exists() for p in _FALLBACK_FONT_CANDIDATES)


needs_symbol_font = pytest.mark.skipif(
    not _symbol_fonts_available(), reason="no symbol-capable font installed on this machine"
)


class TestGlyphCoverage:
    """Glyphs a TUI actually draws must render as themselves, not as `.notdef` boxes."""

    @needs_symbol_font
    def test_app_glyphs_are_not_notdef(self, tmp_path):
        probe = _render_bytes(tmp_path, "\U0010FFFE", "probe.png")
        for index, char in enumerate("\u23F1\U0001F5A5\U0001F4CA\U0001F4CB\U0001F3F4\U0001F3C1\u2B06\u2B07\U0001F4BE"):
            assert _render_bytes(tmp_path, char, f"glyph{index}.png") != probe, f"U+{ord(char):04X} renders as .notdef"

    @needs_symbol_font
    def test_box_drawing_is_drawn(self, tmp_path):
        probe = _render_bytes(tmp_path, "\U0010FFFE", "probe.png")
        for index, char in enumerate("\u2500\u2502\u250C\u2510\u2514\u2518\u2588"):
            assert _render_bytes(tmp_path, char, f"box{index}.png") != probe, f"U+{ord(char):04X} renders as .notdef"


class TestPaletteDescription:
    """The screenshot must say whether the screen carried colour at all."""

    def test_monochrome_screen_is_reported(self):
        from mcp_tui_test._render import describe_screen_attributes

        session = _buffer_session(output="plain text")
        assert "no colour" in describe_screen_attributes(session.screen)

    def test_bold_only_screen_is_named_as_such(self):
        from mcp_tui_test._render import describe_screen_attributes

        session = _buffer_session(output="\x1b[1mBOLD\x1b[22m")
        note = describe_screen_attributes(session.screen)
        assert "bold only" in note and "no colour" in note

    def test_coloured_screen_is_counted(self):
        from mcp_tui_test._render import describe_screen_attributes

        session = _buffer_session(output="\x1b[31mred\x1b[0m")
        assert "cells carry colour" in describe_screen_attributes(session.screen)

    def test_tool_result_includes_the_palette_note(self, fresh_sessions, tmp_path):
        fresh_sessions["s"] = _buffer_session(output="\x1b[31mred")
        result = screenshot(path=str(tmp_path / "c.png"), session_id="s")
        assert "cells carry colour" in result


class TestAttributeRendering:
    """Every attribute the app can set must reach the picture."""

    def _pixels(self, tmp_path, text, name="a.png"):
        from PIL import Image

        target = tmp_path / name
        render_screen_png(_buffer_session(columns=6, lines=2, output=text).screen, target, font_size=24)
        return Image.open(target).convert("RGB")

    def test_underline_changes_pixels(self, tmp_path):
        assert self._pixels(tmp_path, "\x1b[4mU", "u.png").tobytes() != self._pixels(tmp_path, "U", "p.png").tobytes()

    def test_strikethrough_changes_pixels(self, tmp_path):
        assert self._pixels(tmp_path, "\x1b[9mS", "s.png").tobytes() != self._pixels(tmp_path, "S", "p.png").tobytes()

    def test_italic_changes_pixels(self, tmp_path):
        assert self._pixels(tmp_path, "\x1b[3mI", "i.png").tobytes() != self._pixels(tmp_path, "I", "p.png").tobytes()

    def test_reverse_swaps_fg_and_bg(self, tmp_path):
        img = self._pixels(tmp_path, "\x1b[7mR", "r.png")
        colours = {c for _, c in img.getcolors(maxcolors=1 << 20)}
        # Reverse video paints a background block: the cell is no longer all-background.
        assert len(colours) > 1


class TestDefaultColors:
    """A light-theme terminal must not get a black canvas."""

    def test_env_defaults(self, monkeypatch):
        from mcp_tui_test._render import default_colors

        monkeypatch.setenv("MCP_TUI_TEST_FG", "112233")
        monkeypatch.setenv("MCP_TUI_TEST_BG", "fefefe")
        fg, bg = default_colors()
        assert fg == (0x11, 0x22, 0x33)
        assert bg == (0xFE, 0xFE, 0xFE)

    def test_canvas_uses_env_background(self, tmp_path, monkeypatch):
        from PIL import Image

        monkeypatch.setenv("MCP_TUI_TEST_BG", "ffffff")
        target = tmp_path / "light.png"
        render_screen_png(_buffer_session(columns=4, lines=1, output="hi").screen, target, font_size=12)
        img = Image.open(target).convert("RGB")
        assert img.getpixel((0, 0)) == (255, 255, 255)

    def test_bad_env_value_falls_back(self, monkeypatch):
        from mcp_tui_test._render import DEFAULT_BG, DEFAULT_FG, default_colors

        monkeypatch.setenv("MCP_TUI_TEST_FG", "zzz")
        monkeypatch.setenv("MCP_TUI_TEST_BG", "not-hex")
        assert default_colors() == (DEFAULT_FG, DEFAULT_BG)


class TestColorEmoji:
    """The colour-emoji font, when installed, must paint colour rather than a monochrome glyph."""

    @staticmethod
    def _emoji_font_available() -> bool:
        from mcp_tui_test._render import _COLOR_EMOJI_CANDIDATES, _first_existing

        return _first_existing(_COLOR_EMOJI_CANDIDATES) is not None

    @pytest.mark.skipif(not _emoji_font_available.__func__(), reason="no colour-emoji font installed")
    def test_flag_renders_in_colour(self, tmp_path):
        from PIL import Image

        # 🚩 (triangular flag) is chromatic; 🏴 (black flag) is a real glyph but achromatic.
        target = tmp_path / "flag.png"
        render_screen_png(_buffer_session(columns=4, lines=1, output="\U0001F6A9").screen, target, font_size=20)
        colours = {rgb for _, rgb in Image.open(target).convert("RGB").getcolors(maxcolors=1 << 20)}
        saturated = [c for c in colours if max(c) - min(c) > 40]
        assert saturated, "colour emoji painted no chromatic pixel"


class TestCellStyleTool:
    """get_cell_style is the text-form witness of a colour-only state."""

    def test_reports_named_and_hex_colours(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session(columns=10, lines=1, output="\x1b[31mR\x1b[39m\x1b[38;5;196mP")
        assert "fg=red" in get_cell_style(row=0, col=0, session_id="s")
        assert "fg=ff0000" in get_cell_style(row=0, col=1, session_id="s")

    def test_reports_attributes(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session(columns=10, lines=1, output="\x1b[1;4;7mX")
        note = get_cell_style(row=0, col=0, session_id="s")
        assert "bold=yes" in note and "underline=yes" in note and "reverse=yes" in note

    def test_defaults_when_unset(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session(columns=10, lines=1, output="plain")
        assert "fg=default bg=default" in get_cell_style(row=0, col=0, session_id="s")

    def test_out_of_bounds_returns_defaults(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session(columns=4, lines=2)
        assert "out of bounds" in get_cell_style(row=99, col=0, session_id="s")

    def test_stream_mode_rejected(self, fresh_sessions):
        fresh_sessions["s"] = ScreenSession(
            process=DummyProcess(), dimensions=(80, 24), mode="stream", session_id="s"
        )
        with pytest.raises(ToolError, match="buffer mode"):
            get_cell_style(row=0, col=0, session_id="s")


class TestGifTool:
    def test_needs_two_frames(self, fresh_sessions, tmp_path):
        frame = _write_png(tmp_path / "one.png", "one")
        with pytest.raises(ToolError, match="at least 2 frames"):
            gif(frames=[str(frame)], path=str(tmp_path / "movie.gif"))

    def test_missing_frame_rejected(self, fresh_sessions, tmp_path):
        frame = _write_png(tmp_path / "one.png", "one")
        with pytest.raises(ToolError, match="not found"):
            gif(frames=[str(frame), str(tmp_path / "gone.png")], path=str(tmp_path / "movie.gif"))

    def test_bad_duration_rejected(self, fresh_sessions, tmp_path):
        first = _write_png(tmp_path / "a.png", "one")
        second = _write_png(tmp_path / "b.png", "two")
        with pytest.raises(ToolError, match="duration_ms"):
            gif(frames=[str(first), str(second)], path=str(tmp_path / "movie.gif"), duration_ms=0)

    def test_writes_gif(self, fresh_sessions, tmp_path):
        from PIL import Image

        first = _write_png(tmp_path / "a.png", "one")
        second = _write_png(tmp_path / "b.png", "two")
        out = tmp_path / "movie.gif"
        result = gif(frames=[str(first), str(second)], path=str(out), duration_ms=50)
        assert "✓ GIF saved" in result
        assert Image.open(out).n_frames == 2


class TestResizeTool:
    def test_resizes_session(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session(columns=80, lines=24)
        result = resize(dimensions="100x30", session_id="s", delay=0)
        assert "100x30" in result
        assert fresh_sessions["s"].dimensions == (100, 30)
        assert fresh_sessions["s"].screen.columns == 100
        assert fresh_sessions["s"].screen.lines == 30

    def test_bad_dimensions_rejected(self, fresh_sessions):
        fresh_sessions["s"] = _buffer_session()
        with pytest.raises(ToolError, match="Invalid dimensions"):
            resize(dimensions="80x", session_id="s")

    def test_dead_child_rejected(self, fresh_sessions):
        fresh_sessions["s"] = ScreenSession(
            process=DummyProcess(alive=False), dimensions=(20, 3), mode="buffer", session_id="s"
        )
        with pytest.raises(ToolError):
            resize(dimensions="40x10", session_id="s")


def _render_bytes(tmp_path, text: str, name: str) -> bytes:
    """Render `text` alone on a one-row screen and return the PNG bytes."""
    target = tmp_path / name
    render_screen_png(_buffer_session(columns=4, lines=1, output=text).screen, target, font_size=20)
    return target.read_bytes()


def _write_png(path, text: str = "frame"):
    """A real PNG rendered from a buffer session, so the GIF path gets real frames.

    The text differs between frames on purpose: Pillow drops a frame that is
    byte-identical to the previous one, which would make an "N frames" assertion
    vacuous.
    """
    render_screen_png(_buffer_session(output=text).screen, path)
    return path

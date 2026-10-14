"""Raster rendering of a pyte screen: PNG screenshots and animated GIFs.

Text reads describe what the screen *says*; a screenshot is the only witness of
what it *looks like* — colour, contrast, colour-only semantics, the selection
highlight and the cursor. The cell grid pyte already maintains carries fg/bg,
bold, reverse and the cursor, so the picture is drawn from the same buffer the
text tools read: no tmux, no X server, no second capture path.

Pillow is imported lazily so the text-only tools keep working when it is absent.
"""

import os
from pathlib import Path
from typing import Optional, Sequence

# xterm's 16-colour palette, keyed by the names pyte puts on a cell.
ANSI_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "red": (205, 0, 0),
    "green": (0, 205, 0),
    "brown": (205, 205, 0),
    "yellow": (205, 205, 0),
    "blue": (0, 0, 238),
    "magenta": (205, 0, 205),
    "cyan": (0, 205, 205),
    "white": (229, 229, 229),
    "brightblack": (127, 127, 127),
    "brightred": (255, 0, 0),
    "brightgreen": (0, 255, 0),
    "brightbrown": (255, 255, 0),
    "brightyellow": (255, 255, 0),
    "brightblue": (92, 92, 255),
    "brightmagenta": (255, 0, 255),
    "brightcyan": (0, 255, 255),
    "brightwhite": (255, 255, 255),
}

DEFAULT_FG = (229, 229, 229)
DEFAULT_BG = (0, 0, 0)

def _nerd_fonts() -> tuple[str | None, str | None, str | None]:
    """Locate a Nerd Font Mono (regular, bold, italic).

    A Nerd Font is preferred by default: it carries the box-drawing, Powerline
    and icon glyphs a TUI status bar uses, which plain monospace fonts often
    drop. `MCP_TUI_TEST_FONT` overrides the search entirely.
    """
    override = os.environ.get("MCP_TUI_TEST_FONT", "")
    if override:
        return (
            override,
            os.environ.get("MCP_TUI_TEST_FONT_BOLD", "") or None,
            os.environ.get("MCP_TUI_TEST_FONT_ITALIC", "") or None,
        )
    roots = (
        Path.home() / ".local/share/fonts",
        Path.home() / "Library/Fonts",
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts"),
        Path("/Library/Fonts"),
    )
    patterns = (
        "*NerdFontMono-Regular*.ttf",
        "*NerdFontMono-Regular*.otf",
        "*NerdFont-Regular*.ttf",
        "**/*NerdFontMono-Regular*.ttf",
    )
    bold_patterns = ("*NerdFontMono-Bold.ttf", "*NerdFontMono-Bold.otf", "*NerdFont-Bold.ttf")
    italic_patterns = ("*NerdFontMono-Italic.ttf", "*NerdFontMono-Italic.otf", "*NerdFont-Italic.ttf")
    regular = bold = italic = None
    for root in roots:
        if not root.is_dir():
            continue
        regular = regular or _first_glob(root, patterns)
        bold = bold or _first_glob(root, bold_patterns)
        italic = italic or _first_glob(root, italic_patterns)
    return regular, bold, italic


def _first_glob(root: Path, patterns: Sequence[str]) -> str | None:
    for pattern in patterns:
        match = next(iter(sorted(root.glob(pattern))), None)
        if match is not None:
            return str(match)
    return None


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Menlo.ttc",
)
_BOLD_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
)

_ITALIC_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoOblique.ttf",
)

# A colour-emoji font, if one is installed. It only loads at its embedded bitmap
# size, so a glyph is rendered once into a tile and scaled into the cell.
_COLOR_EMOJI_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-emoji/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)
_COLOR_EMOJI_BITMAP_SIZE = 109
_COLOR_EMOJI_MIN_CODEPOINT = 0x1F000

# Fonts tried, in order, for a glyph the monospace primary cannot draw: a TUI's
# status glyphs (⏱ 🖥 📊 📋) and emoji flags (🏴) live outside a mono font's
# coverage, and a screenshot that renders them as `.notdef` boxes misrepresents
# the screen the user actually sees.
_FALLBACK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/truetype/unifont/unifont_upper.ttf",
    "/usr/share/fonts/truetype/unifont/unifont.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
)

# A permanently unassigned code point: every font renders it as the same
# `.notdef` glyph, which is how "does this font have this character?" is
# answered without a fontconfig/cmap dependency.
_UNASSIGNED_PROBE = "\U0010FFFE"

FONT_SIZE_MIN = 4
FONT_SIZE_MAX = 96


def _load_pil():
    """Import Pillow, or explain what to install."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "Screenshots need Pillow. Install it with `uv sync` (or `uv add pillow`) and retry.",
        ) from exc
    return Image, ImageDraw, ImageFont


def resolve_color(value: Optional[str], default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Map a pyte cell colour to RGB.

    pyte writes a 16-colour name (`red`, `brightgreen`, …), a 6-digit hex string
    for a 256-colour index (`ff8700`), or `default`.
    """
    if not value or value == "default":
        return default
    if len(value) == 6:
        try:
            return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
        except ValueError:
            return default
    return ANSI_COLORS.get(value, default)


def _first_existing(candidates: Sequence[str]) -> str | None:
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def primary_font_path(font_path: str | None = None) -> str | None:
    """The file the primary monospace font resolves to, or None for Pillow's default."""
    if font_path:
        return font_path
    return _first_existing(_FONT_CANDIDATES)


class _GlyphFonts:
    """A monospace font with fallbacks, chosen per character by real coverage.

    Coverage is measured, not guessed: a font that cannot draw a character
    returns the same mask as for an unassigned code point, so the first font
    whose mask differs owns that glyph. Results are cached per character, which
    matters because a screen is thousands of cells but a handful of glyphs.
    """

    def __init__(self, primary_path: str | None, fallback_paths: Sequence[str], size: int, ImageFont):
        self._ImageFont = ImageFont
        self._size = size
        self._fonts: list[tuple[object, str]] = []
        if primary_path:
            self._fonts.append((ImageFont.truetype(primary_path, size), primary_path))
        else:
            self._fonts.append((ImageFont.load_default(), ""))
        for path in fallback_paths:
            if Path(path).exists() and all(path != used for _, used in self._fonts):
                self._fonts.append((ImageFont.truetype(path, size), path))
        self._notdef = [self._mask(font, _UNASSIGNED_PROBE) for font, _ in self._fonts]
        self._coverage: dict[str, int | None] = {}

    @staticmethod
    def _mask(font, char: str):
        mask = font.getmask(char)
        return (mask.size, bytes(mask))

    def _owner(self, char: str) -> int | None:
        """Index of the first font that can draw `char`, or None if none can."""
        if char in self._coverage:
            return self._coverage[char]
        owner = None
        for index, (font, _) in enumerate(self._fonts):
            if self._mask(font, char) != self._notdef[index]:
                owner = index
                break
        self._coverage[char] = owner
        return owner

    @property
    def primary(self):
        """The monospace font every cell is measured against."""
        return self._fonts[0][0]

    def pick(self, char: str, max_px: int):
        """A font that can draw `char`, scaled down when the fallback glyph is too wide."""
        owner = self._owner(char)
        if owner is None:
            return self._fonts[0][0]
        font, path = self._fonts[owner]
        if not path:
            return font
        width = font.getlength(char)
        if width <= max_px or width <= 0:
            return font
        shrunk = max(FONT_SIZE_MIN, int(self._size * max_px / width))
        key = (path, shrunk)
        if key not in _SHRUNK_CACHE:
            _SHRUNK_CACHE[key] = self._ImageFont.truetype(path, shrunk)
        return _SHRUNK_CACHE[key]


# Fonts resized to fit a cell are shared across renders: the same glyph at the
# same reduced size does not need to be loaded again.
_SHRUNK_CACHE: dict[tuple[str, int], object] = {}


def _build_fonts(
    font_path: str | None,
    font_size: int,
    ImageFont,
) -> tuple[_GlyphFonts, _GlyphFonts, _GlyphFonts, str]:
    """(normal, bold, italic, primary label) for a render."""
    if font_path:
        normal = _GlyphFonts(font_path, _FALLBACK_FONT_CANDIDATES, font_size, ImageFont)
        return normal, normal, normal, Path(font_path).name
    regular, bold_file, italic_file = _nerd_fonts()
    primary = primary_font_path(regular)
    bold_primary = bold_file or _first_existing(_BOLD_FONT_CANDIDATES)
    italic_primary = italic_file or _first_existing(_ITALIC_FONT_CANDIDATES)
    normal = _GlyphFonts(primary, _FALLBACK_FONT_CANDIDATES, font_size, ImageFont)
    bold = _GlyphFonts(bold_primary or primary, _FALLBACK_FONT_CANDIDATES, font_size, ImageFont)
    italic = _GlyphFonts(italic_primary or primary, _FALLBACK_FONT_CANDIDATES, font_size, ImageFont)
    return normal, bold, italic, Path(primary).name if primary else "Pillow default"


def default_colors() -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(fg, bg) for cells and canvas that carry no colour of their own.

    `MCP_TUI_TEST_FG` / `MCP_TUI_TEST_BG` (hex) set them, so a light-theme
    terminal does not get a black-canvas screenshot of white text.
    """
    return _env_color("MCP_TUI_TEST_FG", DEFAULT_FG), _env_color("MCP_TUI_TEST_BG", DEFAULT_BG)


def _env_color(name: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = os.environ.get(name, "").lstrip("#")
    if len(raw) == 6:
        try:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
        except ValueError:
            return fallback
    return fallback


class _ColorEmoji:
    """Colour emoji glyphs as ready-to-paste tiles.

    Noto Color Emoji carries one bitmap strike, so a glyph is drawn once with
    `embedded_color`, cropped to its ink box and scaled to the cell. A glyph the
    font cannot draw yields no tile, and the caller falls back to the
    monochrome chain.
    """

    def __init__(self, ImageFont, Image, ImageDraw):
        self._Image = Image
        self._draw_class = ImageDraw
        self.font = None
        self._tiles: dict[str, object | None] = {}
        self._scaled: dict[tuple[str, int, int], object | None] = {}
        path = _first_existing(_COLOR_EMOJI_CANDIDATES)
        if path:
            try:
                self.font = ImageFont.truetype(path, _COLOR_EMOJI_BITMAP_SIZE)
            except OSError:  # not a bitmap-strike font, or an unsupported size
                self.font = None

    def scaled(self, char: str, width: int, height: int):
        """A `width`x`height` RGBA tile for `char`, or None when unavailable."""
        key = (char, width, height)
        if key in self._scaled:
            return self._scaled[key]
        tile = self._tile(char)
        scaled = tile.resize((width, height), self._Image.LANCZOS) if tile is not None else None
        self._scaled[key] = scaled
        return scaled

    def _tile(self, char: str):
        if char in self._tiles:
            return self._tiles[char]
        tile = None
        if self.font is not None:
            size = _COLOR_EMOJI_BITMAP_SIZE
            canvas = self._Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
            self._draw_class.Draw(canvas).text((0, 0), char, font=self.font, embedded_color=True)
            box = canvas.getbbox()
            if box:
                tile = canvas.crop(box)
        self._tiles[char] = tile
        return tile


def describe_screen_attributes(screen) -> str:
    """A one-line note about the colours and emphasis the cell grid actually holds.

    A screenshot of a monochrome app is monochrome: without this, a reader cannot
    tell "the app sends no colour" (an app finding) from "the renderer dropped the
    colour" (a harness bug).
    """
    coloured = 0
    bold = 0
    for y in range(screen.lines):
        row = screen.buffer[y]
        for x in range(screen.columns):
            cell = row[x]
            if (cell.fg and cell.fg != "default") or (cell.bg and cell.bg != "default"):
                coloured += 1
            if cell.bold:
                bold += 1
    if coloured:
        return f"{coloured} cells carry colour"
    if bold:
        return "no colour: the app emits bold only (a monochrome app renders monochrome)"
    return "no colour or emphasis: the app emits plain text"


def render_screen_png(
    screen,
    output_path: str | Path,
    font_size: int = 14,
    font_path: Optional[str] = None,
    show_cursor: bool = True,
) -> str:
    """Draw a pyte screen to a PNG and return the path written.

    One pixel rectangle per cell, using each cell's fg/bg, bold weight and
    reverse flag, and a per-glyph font fallback so symbols a monospace font
    lacks (⏱ 🖥 📊 🏴) are drawn rather than replaced by `.notdef` boxes. A wide
    glyph advances two columns of the canvas, so a cell's pixel column matches
    its screen column. The cursor is painted as an inverted block (the only
    visual witness of *focus*, which text reads cannot show).
    """
    Image, ImageDraw, ImageFont = _load_pil()

    normal, bold, italic, _ = _build_fonts(font_path, font_size, ImageFont)
    fg_default, bg_default = default_colors()
    char_w = max(1, round(normal.primary.getlength("M")))
    char_h = max(1, round(font_size * 1.35))
    cols, rows = screen.columns, screen.lines
    emoji = _ColorEmoji(ImageFont, Image, ImageDraw)

    image = Image.new("RGB", (cols * char_w, rows * char_h), bg_default)
    draw = ImageDraw.Draw(image)

    for y in range(rows):
        row = screen.buffer[y]
        x = 0
        while x < cols:
            cell = row[x]
            data = cell.data
            if not data:  # malformed or live wide-char stub: keep the column, draw nothing
                x += 1
                continue
            wide = len(data) == 1 and _is_wide(data)
            advance = char_w * (2 if wide else 1)
            fg = resolve_color(cell.fg, fg_default)
            bg = resolve_color(cell.bg, bg_default)
            if cell.reverse:
                fg, bg = bg, fg
            px, py = x * char_w, y * char_h
            if bg != bg_default:
                draw.rectangle([px, py, px + char_w - 1, py + char_h - 1], fill=bg)
            if data.strip():
                tile = None
                if len(data) == 1 and ord(data) >= _COLOR_EMOJI_MIN_CODEPOINT:
                    tile = emoji.scaled(data, advance, char_h)
                if tile is not None:
                    image.paste(tile, (px, py), tile)
                else:
                    chain = italic if cell.italics else bold if cell.bold else normal
                    font = chain.pick(data, advance)
                    draw.text((px, py), data, font=font, fill=fg)
                if cell.underscore:
                    draw.line([(px, py + char_h - 2), (px + advance - 2, py + char_h - 2)], fill=fg)
                if cell.strikethrough:
                    draw.line([(px, py + char_h // 2), (px + advance - 2, py + char_h // 2)], fill=fg)
            x += 2 if wide else 1

    cursor = getattr(screen, "cursor", None)
    if show_cursor and cursor is not None and not getattr(cursor, "hidden", False):
        cx, cy = cursor.x, cursor.y
        if 0 <= cx < cols and 0 <= cy < rows:
            px, py = cx * char_w, cy * char_h
            draw.rectangle([px, py, px + char_w - 1, py + char_h - 1], fill=fg_default)
            under = screen.buffer[cy][cx].data
            if under.strip():
                draw.text((px, py), under, font=normal.primary, fill=bg_default)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return str(output_path)


def _is_wide(char: str) -> bool:
    """True when the glyph occupies two terminal columns."""
    try:
        from wcwidth import wcwidth
    except ImportError:  # pragma: no cover - wcwidth ships with pyte
        return False
    return wcwidth(char) == 2


def frames_to_gif(
    frame_paths: Sequence[str | Path],
    output_path: str | Path,
    duration_ms: int = 500,
    loop: int = 0,
) -> str:
    """Stitch PNG screenshots into one animated GIF and return the path written.

    Frames are resized onto the first frame's canvas, so a session whose size
    changed mid-run still produces a valid animation.
    """
    Image, _, _ = _load_pil()
    if not frame_paths:
        raise ValueError("frames_to_gif needs at least one frame")
    if duration_ms < 1:
        raise ValueError("duration_ms must be >= 1")

    frames = [Image.open(path).convert("RGB") for path in frame_paths]
    size = frames[0].size
    frames = [frame if frame.size == size else frame.resize(size) for frame in frames]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=loop,
    )
    return str(output_path)

"""Tests for ScreenModel and CursorPosition dataclasses."""

import pytest

from mcp_tui_test._server import CursorPosition, ScreenModel


class TestCursorPosition:
    def test_construct(self):
        cp = CursorPosition(row=3, col=5)
        assert cp.row == 3
        assert cp.col == 5

    def test_immutable(self):
        cp = CursorPosition(row=1, col=2)
        with pytest.raises(AttributeError):
            cp.row = 99

    def test_slotted(self):
        cp = CursorPosition(row=0, col=0)
        with pytest.raises(AttributeError):
            cp.__dict__


class TestScreenModel:
    def test_construct(self):
        cp = CursorPosition(row=0, col=0)
        sm = ScreenModel(
            lines=("line1", "line2"),
            cursor=cp,
            width=80,
            height=24,
            mode="buffer",
        )
        assert sm.lines == ("line1", "line2")
        assert sm.cursor == cp
        assert sm.width == 80
        assert sm.height == 24
        assert sm.mode == "buffer"

    def test_text_property(self):
        sm = ScreenModel(
            lines=("hello", "world"),
            cursor=CursorPosition(row=0, col=0),
            width=80,
            height=24,
            mode="buffer",
        )
        assert sm.text == "hello\nworld"

    def test_text_empty_lines(self):
        sm = ScreenModel(
            lines=("",),
            cursor=CursorPosition(row=-1, col=-1),
            width=80,
            height=24,
            mode="stream",
        )
        assert sm.text == ""

    def test_immutable(self):
        sm = ScreenModel(
            lines=("a",),
            cursor=CursorPosition(row=0, col=0),
            width=80,
            height=24,
            mode="buffer",
        )
        with pytest.raises(AttributeError):
            sm.lines = ("b",)

    def test_slotted(self):
        sm = ScreenModel(
            lines=("a",),
            cursor=CursorPosition(row=0, col=0),
            width=80,
            height=24,
            mode="buffer",
        )
        with pytest.raises(AttributeError):
            sm.__dict__

    def test_frozen_cursor_in_model(self):
        """CursorPosition nested inside ScreenModel should still be frozen."""
        cp = CursorPosition(row=5, col=10)
        sm = ScreenModel(
            lines=("a",),
            cursor=cp,
            width=80,
            height=24,
            mode="buffer",
        )
        with pytest.raises(AttributeError):
            sm.cursor.row = 99

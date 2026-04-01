"""Tests for UI helper functions."""

from flash_tool import _display_width


class TestDisplayWidth:
    def test_ascii(self):
        assert _display_width("hello") == 5

    def test_empty(self):
        assert _display_width("") == 0

    def test_emoji_is_wide(self):
        assert _display_width("👉") == 2

    def test_mixed_emoji_and_ascii(self):
        # "👉 ACTION REQUIRED" = 2 + 1 + 15 = 18
        assert _display_width("👉 ACTION REQUIRED") == 18

    def test_lightning_emoji(self):
        assert _display_width("⚡") == 2  # wide symbol

    def test_cjk_characters(self):
        # Each CJK character is 2 columns wide
        assert _display_width("日本語") == 6

    def test_box_drawing_characters(self):
        # Box-drawing chars (─, │, etc.) are ambiguous width but typically 1
        assert _display_width("─") == 1

    def test_content_line_with_unicode_arrow(self):
        line = "Front JTAG port (USB-C #3) → your computer"
        # → is a narrow character, rest is ASCII
        assert _display_width(line) == len(line)

"""Tests for the shared widgets in :mod:`auto_essay_typer.gui.widgets`."""

from __future__ import annotations

import pytest

from auto_essay_typer.gui.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeManager, ThemeMode
from auto_essay_typer.gui.widgets import Banner, CollapsibleSection, LinkButton, rgba, tone_color


def test_rgba_converts_hex_to_a_stylesheet_colour():
    assert rgba("#FF8000", 0.5) == "rgba(255, 128, 0, 0.50)"


def test_tone_colors_come_from_the_tokens():
    assert tone_color(LIGHT_TOKENS, "danger") == LIGHT_TOKENS.danger
    assert tone_color(DARK_TOKENS, "warning") == DARK_TOKENS.attention
    assert tone_color(LIGHT_TOKENS, "info") == LIGHT_TOKENS.accent


def test_a_banner_is_hidden_until_it_has_a_message(qtbot):
    banner = Banner("warning")
    qtbot.addWidget(banner)
    assert banner.isHidden()
    banner.set_message("Heads up")
    assert not banner.isHidden()
    assert banner.message() == "Heads up"
    banner.set_message("")
    assert banner.isHidden()


def test_dismissing_a_banner_hides_it_and_says_so(qtbot):
    banner = Banner("danger")
    qtbot.addWidget(banner)
    banner.set_message("Something went wrong")
    with qtbot.waitSignal(banner.dismissed, timeout=1000):
        banner.close_button.click()
    assert banner.isHidden()


def test_a_non_dismissible_banner_has_no_close_button(qtbot):
    banner = Banner("info", dismissible=False)
    qtbot.addWidget(banner)
    assert banner.close_button.isHidden()


def test_a_banner_rejects_an_unknown_tone(qtbot):
    with pytest.raises(ValueError, match="unknown tone"):
        Banner("loud")
    banner = Banner("info")
    qtbot.addWidget(banner)
    with pytest.raises(ValueError, match="unknown tone"):
        banner.set_message("x", tone="loud")


def test_a_banner_action_runs_its_callback(qtbot):
    banner = Banner("info")
    qtbot.addWidget(banner)
    calls = []
    button = banner.add_action("Open Settings", lambda: calls.append("open"))
    button.click()
    assert calls == ["open"]


def test_a_collapsible_section_folds_and_unfolds(qtbot):
    section = CollapsibleSection("Speed", expanded=True)
    qtbot.addWidget(section)
    assert section.is_expanded() is True
    assert section.body.isHidden() is False

    with qtbot.waitSignal(section.toggled, timeout=1000) as blocker:
        section.header_button.click()
    assert blocker.args == [False]
    assert section.is_expanded() is False
    assert section.body.isHidden() is True

    section.set_expanded(True)
    assert section.body.isHidden() is False


def test_a_collapsed_section_starts_folded(qtbot):
    section = CollapsibleSection("Breaks", expanded=False, subtitle="When to pause")
    qtbot.addWidget(section)
    assert section.body.isHidden() is True
    assert section.subtitle_label.isHidden() is True
    section.set_expanded(True)
    assert section.subtitle_label.isHidden() is False


def test_widgets_restyle_when_the_theme_changes(qtbot, qapp):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    banner = Banner("info", theme_manager=theme_manager)
    link = LinkButton("Reveal", theme_manager=theme_manager)
    section = CollapsibleSection("Group", theme_manager=theme_manager)
    for widget in (banner, link, section):
        qtbot.addWidget(widget)

    assert LIGHT_TOKENS.text in banner.styleSheet()
    assert LIGHT_TOKENS.accent in link.styleSheet()

    theme_manager.set_mode(ThemeMode.DARK)

    assert DARK_TOKENS.text in banner.styleSheet()
    assert LIGHT_TOKENS.text not in banner.styleSheet()
    assert DARK_TOKENS.text in section.header_button.styleSheet()
    theme_manager.set_mode(ThemeMode.SYSTEM)

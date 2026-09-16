from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

from auto_essay_typer.gui.theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    ThemeManager,
    ThemeMode,
    build_palette,
    build_stylesheet,
    resolve_scheme,
    tokens_for,
)


def test_resolve_scheme_light_and_dark_are_pinned():
    assert resolve_scheme(ThemeMode.LIGHT) == Qt.ColorScheme.Light
    assert resolve_scheme(ThemeMode.DARK) == Qt.ColorScheme.Dark


def test_tokens_for_dark_scheme_uses_dark_tokens():
    assert tokens_for(Qt.ColorScheme.Dark) is DARK_TOKENS
    assert tokens_for(Qt.ColorScheme.Light) is LIGHT_TOKENS


def test_light_and_dark_tokens_share_the_same_brand_colors():
    assert LIGHT_TOKENS.accent == DARK_TOKENS.accent == "#6D5AE6"
    assert LIGHT_TOKENS.danger == DARK_TOKENS.danger == "#E5484D"
    assert LIGHT_TOKENS.success == DARK_TOKENS.success == "#2EAD6B"


def test_light_and_dark_tokens_have_different_surfaces():
    assert LIGHT_TOKENS.window != DARK_TOKENS.window
    assert LIGHT_TOKENS.surface != DARK_TOKENS.surface
    assert LIGHT_TOKENS.text != DARK_TOKENS.text


def test_build_palette_uses_accent_as_highlight():
    palette = build_palette(LIGHT_TOKENS)
    assert palette.color(QPalette.ColorRole.Highlight).name().upper() == LIGHT_TOKENS.accent.upper()


def test_build_stylesheet_embeds_every_surface_color():
    css = build_stylesheet(DARK_TOKENS)
    assert DARK_TOKENS.window in css
    assert DARK_TOKENS.surface in css
    assert DARK_TOKENS.accent in css


def test_theme_manager_defaults_to_system_and_applies_a_stylesheet(qtbot, qapp):
    manager = ThemeManager(qapp)
    assert manager.mode is ThemeMode.SYSTEM
    assert qapp.styleSheet()  # apply() ran during construction


def test_theme_manager_set_mode_switches_tokens_and_emits_changed(qtbot, qapp):
    manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    assert manager.tokens is LIGHT_TOKENS
    assert manager.is_dark is False

    with qtbot.waitSignal(manager.themeChanged, timeout=1000):
        manager.set_mode(ThemeMode.DARK)
    assert manager.tokens is DARK_TOKENS
    assert manager.is_dark is True


def test_theme_manager_set_mode_to_same_value_is_a_no_op(qtbot, qapp):
    manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    received = []
    manager.themeChanged.connect(lambda: received.append(1))
    manager.set_mode(ThemeMode.LIGHT)
    assert received == []


def test_theme_manager_switching_changes_palette_window_color(qtbot, qapp):
    manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    light_color = qapp.palette().window().color().name()
    manager.set_mode(ThemeMode.DARK)
    dark_color = qapp.palette().window().color().name()
    assert light_color != dark_color
    manager.set_mode(ThemeMode.LIGHT)
    assert qapp.palette().window().color().name() == light_color

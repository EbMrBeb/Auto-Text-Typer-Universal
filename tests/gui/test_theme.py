import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QWidget

from auto_essay_typer.gui.theme import (
    BASE_FONT_PIXEL_SIZE,
    DARK_TOKENS,
    LIGHT_TOKENS,
    ThemeManager,
    ThemeMode,
    build_app_font,
    build_palette,
    build_stylesheet,
    contrast_ratio,
    relative_luminance,
    resolve_scheme,
    resolve_ui_font_families,
    tint,
    tokens_for,
)
from auto_essay_typer.platforms import OperatingSystem


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


# --- color math ---------------------------------------------------------------------------


def test_contrast_ratio_matches_the_wcag_extremes():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)  # symmetric
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert relative_luminance("#000000") == 0.0
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0)


def test_tint_composites_a_color_over_a_background():
    assert tint("#000000", "#FFFFFF", 0.0) == "#FFFFFF"
    assert tint("#000000", "#FFFFFF", 1.0) == "#000000"
    assert tint("#000000", "#FFFFFF", 0.5) == "#808080"


# --- WCAG 2.1 AA, every pair the stylesheet draws, both themes ---------------------------
#
# 4.5:1 for text (1.4.3); 3:1 for the outlines and indicators that identify a control and
# its state (1.4.11). Disabled controls are exempt from both by the standard, and are not
# listed. A failure here means a palette edit made something unreadable: fix the token,
# not the test.

_THEMES = [pytest.param(LIGHT_TOKENS, id="light"), pytest.param(DARK_TOKENS, id="dark")]


def _text_pairs(t):
    chip_ready = tint(t.success, t.surface, 0.14)
    chip_warning = tint(t.attention, t.surface, 0.18)
    badge_success = tint(t.success, t.window, 0.16)
    badge_danger = tint(t.danger, t.window, 0.16)
    return {
        "body text on window": (t.text, t.window),
        "body text on surface": (t.text, t.surface),
        "body text on sidebar": (t.text, t.sidebar),
        "muted text on window": (t.muted_text, t.window),
        "muted text on surface": (t.muted_text, t.surface),
        "muted text on sidebar (and the disabled text box)": (t.muted_text, t.sidebar),
        "nav item selected / primary button": (t.on_accent, t.accent),
        "primary button hovered": (t.on_accent, t.accent_hover),
        "primary button pressed": (t.on_accent, t.accent_pressed),
        "accent text on surface (secondary button hover, links)": (t.accent_text, t.surface),
        "accent text on window": (t.accent_text, t.window),
        "accent text on sidebar": (t.accent_text, t.sidebar),
        "danger text on surface (Stop button)": (t.danger_text, t.surface),
        "danger text on window (inline error)": (t.danger_text, t.window),
        "danger text on its badge tint": (t.danger_text, badge_danger),
        "Stop button hovered": (t.on_accent, t.danger_hover),
        "Stop button pressed": (t.surface, t.danger_text),
        "ready chip": (t.success_text, chip_ready),
        "verified badge": (t.success_text, badge_success),
        "warning chip": (t.attention_text, chip_warning),
    }


def _non_text_pairs(t):
    return {
        "control outline on surface": (t.border_strong, t.surface),
        "control outline on window": (t.border_strong, t.window),
        "accent outline / focus ring on surface": (t.accent_text, t.surface),
        "accent outline / focus ring on window": (t.accent_text, t.window),
        "focus ring on surface": (t.focus_ring, t.surface),
        "focus ring on window": (t.focus_ring, t.window),
        "focus ring on the accent fill": (t.focus_ring, t.accent),
        "slider / progress fill on its track": (t.accent_indicator, t.border),
        "slider / progress fill on the card": (t.accent_indicator, t.surface),
        "switch on-track on the card": (t.accent, t.surface),
        "switch knob on the on-track": (t.on_accent, t.accent),
        "switch knob on the off-track": (t.on_accent, t.border_strong),
    }


@pytest.mark.parametrize("tokens", _THEMES)
def test_every_text_pair_meets_wcag_aa(tokens):
    failures = {
        name: round(contrast_ratio(fg, bg), 2)
        for name, (fg, bg) in _text_pairs(tokens).items()
        if contrast_ratio(fg, bg) < 4.5
    }
    assert failures == {}, f"text below 4.5:1: {failures}"


@pytest.mark.parametrize("tokens", _THEMES)
def test_every_control_outline_and_indicator_meets_wcag_aa(tokens):
    failures = {
        name: round(contrast_ratio(fg, bg), 2)
        for name, (fg, bg) in _non_text_pairs(tokens).items()
        if contrast_ratio(fg, bg) < 3.0
    }
    assert failures == {}, f"non-text below 3:1: {failures}"


@pytest.mark.parametrize("tokens", _THEMES)
def test_disabled_text_is_dimmer_than_secondary_text(tokens):
    # "Reads as disabled": strictly less contrast than muted text on the same surface.
    assert contrast_ratio(tokens.disabled_text, tokens.surface) < contrast_ratio(
        tokens.muted_text, tokens.surface
    )


# --- stylesheet ---------------------------------------------------------------------------


@pytest.mark.parametrize("tokens", _THEMES)
def test_stylesheet_has_no_unresolved_placeholders(tokens):
    css = build_stylesheet(tokens)
    assert "$" not in css


@pytest.mark.parametrize("tokens", _THEMES)
def test_stylesheet_image_urls_point_at_files_that_exist(tokens):
    urls = re.findall(r'url\("([^"]+)"\)', build_stylesheet(tokens))
    assert urls, "expected the combo chevron and the checkbox tick"
    for url in urls:
        assert Path(url).is_file(), url


def test_button_height_alias_follows_control_height():
    assert LIGHT_TOKENS.button_height == LIGHT_TOKENS.control_height == 32


def _themed(qtbot, qapp, mode=ThemeMode.LIGHT):
    return ThemeManager(qapp, mode=mode)


def _controls(qtbot):
    """(host, controls): keep ``host`` alive for as long as the controls are used."""
    host = QWidget()
    qtbot.addWidget(host)
    primary = QPushButton("Start typing", host)
    primary.setObjectName("PrimaryButton")
    secondary = QPushButton("Preview", host)
    secondary.setObjectName("SecondaryButton")
    destructive = QPushButton("Stop", host)
    destructive.setObjectName("DestructiveButton")
    nav = QPushButton("Settings", host)
    nav.setObjectName("NavButton")
    combo = QComboBox(host)
    combo.addItems(["natural (Built-in)"])
    line = QLineEdit("text", host)
    controls = {
        "primary": primary,
        "secondary": secondary,
        "destructive": destructive,
        "nav": nav,
        "combo": combo,
        "line edit": line,
    }
    return host, controls


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_buttons_combos_and_inputs_share_one_control_height(qtbot, qapp, mode):
    manager = _themed(qtbot, qapp, mode)
    expected = manager.tokens.control_height
    heights = {}
    _host, controls = _controls(qtbot)
    for name, widget in controls.items():
        widget.ensurePolished()
        heights[name] = widget.sizeHint().height()
    assert heights == dict.fromkeys(heights, expected), heights


def test_keyboard_focus_ring_never_changes_a_controls_size(qtbot, qapp):
    # The ring swaps a 1px border for a 2px one; padding and min-height give the pixel back.
    from auto_essay_typer.gui.focus_ring import set_focus_ring

    _themed(qtbot, qapp)
    _host, controls = _controls(qtbot)
    for name, widget in controls.items():
        widget.ensurePolished()
        before = widget.sizeHint()
        set_focus_ring(widget, True)
        widget.ensurePolished()
        assert widget.sizeHint() == before, name


def test_permission_chip_is_a_true_pill(qtbot, qapp):
    # Qt draws a rounded rect only while border-radius <= half the height, and falls back to
    # square corners beyond it. The chip once silently lost its rounding that way.
    from PySide6.QtWidgets import QHBoxLayout

    _themed(qtbot, qapp)
    host = QWidget()
    qtbot.addWidget(host)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(10, 10, 10, 10)
    chip = QPushButton("Ready to type")
    chip.setObjectName("PermissionChip")
    chip.setProperty("state", "ready")
    layout.addWidget(chip)
    host.show()
    qapp.processEvents()
    image = host.grab().toImage()
    background = image.pixelColor(2, 2)  # host margin, well outside the chip
    corner = image.pixelColor(chip.x(), chip.y())
    middle = image.pixelColor(chip.x() + chip.width() // 2, chip.y() + 2)
    assert corner == background, "chip corner is painted: border-radius is too large for its height"
    assert middle != background


# --- application font ---------------------------------------------------------------------

_MAC = ".AppleSystemUIFont", "Helvetica Neue", "Helvetica", "Arial"


def test_font_families_are_filtered_to_those_that_exist():
    families = resolve_ui_font_families(OperatingSystem.MACOS, ["Helvetica", "Zapfino"])
    assert families == ["Helvetica"]


def test_font_family_match_is_case_insensitive_and_returns_the_installed_spelling():
    families = resolve_ui_font_families(OperatingSystem.WINDOWS, ["segoe ui", "ARIAL"])
    assert families == ["segoe ui", "ARIAL"]


def test_platform_default_font_comes_first_when_it_exists():
    families = resolve_ui_font_families(
        OperatingSystem.LINUX, ["Cantarell", "DejaVu Sans", "Fira Sans"], platform_default="Fira Sans"
    )
    assert families[0] == "Fira Sans"
    assert families[1:] == ["Cantarell", "DejaVu Sans"]


def test_a_missing_platform_default_is_never_requested():
    # Qt's own placeholder "Sans Serif" is what an OS-less platform reports; it is not a font.
    families = resolve_ui_font_families(
        OperatingSystem.MACOS, [".AppleSystemUIFont", "Arial"], platform_default="Sans Serif"
    )
    assert "Sans Serif" not in families
    assert families == [".AppleSystemUIFont", "Arial"]


def test_macos_prefers_the_system_ui_font():
    assert resolve_ui_font_families(OperatingSystem.MACOS, _MAC)[0] == ".AppleSystemUIFont"


def test_windows_prefers_segoe_ui():
    assert resolve_ui_font_families(OperatingSystem.WINDOWS, ["Arial", "Segoe UI", "Tahoma"])[0] == "Segoe UI"


def test_linux_stack_falls_back_through_common_sans_serifs():
    available = ["Liberation Sans", "DejaVu Sans"]
    assert resolve_ui_font_families(OperatingSystem.LINUX, available) == ["DejaVu Sans", "Liberation Sans"]


def test_no_known_family_installed_yields_no_font_rather_than_a_missing_one():
    assert resolve_ui_font_families(OperatingSystem.MACOS, ["Zapfino"]) == []
    assert build_app_font(OperatingSystem.MACOS, ["Zapfino"], platform_default="Sans Serif") is None


def test_app_font_is_pixel_sized_so_it_matches_on_every_os(qtbot, qapp):
    font = build_app_font(OperatingSystem.WINDOWS, ["Segoe UI", "Arial"], platform_default="Segoe UI")
    assert font is not None
    assert font.family() == "Segoe UI"
    assert font.pixelSize() == BASE_FONT_PIXEL_SIZE
    assert font.families() == ["Segoe UI", "Arial"]


def test_theme_manager_installs_a_font_that_really_exists(qtbot, qapp):
    if build_app_font() is None:
        pytest.skip("none of the preferred UI font families is installed on this machine")
    ThemeManager(qapp)
    assert qapp.font().family() in QFontDatabase.families()
    assert qapp.font().family() != "Sans Serif"
    assert qapp.font().pixelSize() == BASE_FONT_PIXEL_SIZE

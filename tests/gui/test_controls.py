"""Tests for the custom-painted controls and layout helpers in ``gui/controls.py``."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.gui.controls import ScrollPageLayout, ToggleSwitch, switch_row
from auto_essay_typer.gui.focus_ring import set_focus_ring
from auto_essay_typer.gui.theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    ThemeManager,
    ThemeMode,
    contrast_ratio,
)

# Where the track sits inside the widget: the focus ring's margin from the left edge, and a
# vertically centered 36x20 pill. These mirror the constants in controls.py on purpose: a
# change to the switch's geometry should have to touch this test too.
_RING_MARGIN = 3
_TRACK_WIDTH = 36


def _switch(qtbot, qapp, *, checked=False, text="Take breaks", mode=ThemeMode.LIGHT, show=True):
    ThemeManager(qapp, mode=mode)
    switch = ToggleSwitch(text)
    switch.setChecked(checked)
    qtbot.addWidget(switch)
    if show:
        switch.show()
        qtbot.waitExposed(switch)
        qapp.processEvents()
    return switch


def _pixel(switch, x, y=None):
    image = switch.grab().toImage()
    return image.pixelColor(x, image.height() // 2 if y is None else y)


def _track_left(switch):
    """A pixel inside the track, left of the knob's *checked* position and clear of it."""
    return _pixel(switch, _RING_MARGIN + 3)


def _track_right(switch):
    """A pixel inside the track, right of the knob's *unchecked* position and clear of it."""
    return _pixel(switch, _RING_MARGIN + _TRACK_WIDTH - 4)


def _wait_idle(qtbot, switch):
    qtbot.waitUntil(lambda: not switch._animating, timeout=2000)


# --- behavior: it is a checkbox -------------------------------------------------------------


def test_toggle_switch_is_a_drop_in_checkbox(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    assert isinstance(switch, QCheckBox)
    assert switch.objectName() == "ToggleSwitch"
    assert switch.text() == "Take breaks"
    assert switch.isChecked() is False


def test_clicking_the_switch_toggles_it_and_emits_toggled(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    with qtbot.waitSignal(switch.toggled, timeout=1000) as blocker:
        qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(_RING_MARGIN + 10, switch.height() // 2))
    assert blocker.args == [True]
    assert switch.isChecked() is True
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(_RING_MARGIN + 10, switch.height() // 2))
    assert switch.isChecked() is False


def test_clicking_the_label_toggles_it_too(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(switch.width() - 4, switch.height() // 2))
    assert switch.isChecked() is True


def test_space_bar_toggles_the_switch(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked() is True
    qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked() is False


def test_a_disabled_switch_ignores_clicks_and_keys(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    switch.setEnabled(False)
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(10, switch.height() // 2))
    qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked() is False


def test_the_switch_is_reachable_by_tab(qtbot, qapp):
    switch = _switch(qtbot, qapp, show=False)
    assert switch.focusPolicy() & Qt.FocusPolicy.TabFocus


# --- geometry -------------------------------------------------------------------------------


def test_size_hint_grows_with_the_label(qtbot, qapp):
    short = _switch(qtbot, qapp, text="On", show=False)
    long = _switch(qtbot, qapp, text="Show the mini progress window while typing", show=False)
    bare = _switch(qtbot, qapp, text="", show=False)
    assert bare.sizeHint().width() < short.sizeHint().width() < long.sizeHint().width()
    assert bare.sizeHint().height() >= 26  # the 20px track plus the ring's margin


def test_minimum_size_equals_size_hint_so_the_label_never_clips(qtbot, qapp):
    switch = _switch(qtbot, qapp, show=False)
    assert switch.minimumSizeHint() == switch.sizeHint()


# --- painting -------------------------------------------------------------------------------


def test_unchecked_track_uses_the_outline_color_and_checked_uses_the_accent(qtbot, qapp):
    off = _switch(qtbot, qapp, checked=False)
    on = _switch(qtbot, qapp, checked=True)
    assert _track_right(off) == QColor(LIGHT_TOKENS.border_strong)
    assert _track_left(on) == QColor(LIGHT_TOKENS.accent)


def test_the_knob_sits_left_when_off_and_right_when_on(qtbot, qapp):
    off = _switch(qtbot, qapp, checked=False)
    on = _switch(qtbot, qapp, checked=True)
    white = QColor(LIGHT_TOKENS.on_accent)
    knob_left_x = _RING_MARGIN + 3 + 7  # inset + half the 14px knob
    knob_right_x = _RING_MARGIN + _TRACK_WIDTH - 3 - 7
    assert _pixel(off, knob_left_x) == white
    assert _pixel(off, knob_right_x) != white
    assert _pixel(on, knob_right_x) == white
    assert _pixel(on, knob_left_x) != white


def test_changing_state_with_signals_blocked_still_paints_correctly(qtbot, qapp):
    # The Type page resets its switch this way when a profile is chosen.
    switch = _switch(qtbot, qapp, checked=False)
    switch.blockSignals(True)
    switch.setChecked(True)
    switch.blockSignals(False)
    switch.repaint()
    assert _track_left(switch) == QColor(LIGHT_TOKENS.accent)


def test_toggling_a_visible_switch_animates_and_lands_on_the_new_state(qtbot, qapp):
    switch = _switch(qtbot, qapp, checked=False)
    switch.setChecked(True)
    _wait_idle(qtbot, switch)
    assert _track_left(switch) == QColor(LIGHT_TOKENS.accent)
    switch.setChecked(False)
    _wait_idle(qtbot, switch)
    assert _track_right(switch) == QColor(LIGHT_TOKENS.border_strong)


def test_toggling_back_mid_animation_still_settles_on_the_final_state(qtbot, qapp):
    switch = _switch(qtbot, qapp, checked=False)
    switch.setChecked(True)
    switch.setChecked(False)
    _wait_idle(qtbot, switch)
    assert switch.isChecked() is False
    assert _track_right(switch) == QColor(LIGHT_TOKENS.border_strong)


def test_the_switch_follows_a_live_theme_change(qtbot, qapp):
    manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    switch = ToggleSwitch("Take breaks")
    qtbot.addWidget(switch)
    switch.show()
    qtbot.waitExposed(switch)
    assert _track_right(switch) == QColor(LIGHT_TOKENS.border_strong)
    manager.set_mode(ThemeMode.DARK)
    qapp.processEvents()
    assert _track_right(switch) == QColor(DARK_TOKENS.border_strong)


def test_a_disabled_switch_is_dimmed_not_just_unclickable(qtbot, qapp):
    enabled = _switch(qtbot, qapp, checked=True)
    disabled = _switch(qtbot, qapp, checked=True)
    disabled.setEnabled(False)
    qapp.processEvents()
    assert _track_left(disabled) != _track_left(enabled)
    # ... but it is still visibly "on": lighter than the off-track, not the same as it.
    assert _track_left(disabled) != QColor(LIGHT_TOKENS.border)


def test_keyboard_focus_draws_a_ring_outside_the_track(qtbot, qapp):
    switch = _switch(qtbot, qapp)
    ring_pixel = lambda: _pixel(switch, 1)  # noqa: E731 - the ring's outer band, left of the track
    resting = ring_pixel()
    set_focus_ring(switch, True)
    qapp.processEvents()
    focused = ring_pixel()
    assert focused != resting
    link = QColor(LIGHT_TOKENS.accent_text)
    assert abs(focused.red() - link.red()) < 90  # the ring color, not merely "some change"
    set_focus_ring(switch, False)
    qapp.processEvents()
    assert ring_pixel() == resting


_THEME_CASES = [
    pytest.param(ThemeMode.LIGHT, LIGHT_TOKENS, id="light"),
    pytest.param(ThemeMode.DARK, DARK_TOKENS, id="dark"),
]


def _rgb(color: QColor) -> tuple[int, int, int]:
    return color.red(), color.green(), color.blue()


@pytest.mark.parametrize(("mode", "tokens"), _THEME_CASES)
@pytest.mark.parametrize("checked", [False, True], ids=["off", "on"])
def test_the_focus_ring_stands_clear_of_the_track_and_the_page_in_both_themes(
    qtbot, qapp, mode, tokens, checked
):
    """The ring must be told apart from the track it encircles *and* from the page behind it.

    Measured from rendered pixels rather than tokens. The ring was once painted in the accent
    colour, which is (nearly) the colour of the "on" track: no token-level check of "ring on
    page" notices that the ring and the control it surrounds are one violet blob.
    """
    switch = _switch(qtbot, qapp, checked=checked, mode=mode)
    set_focus_ring(switch, True)
    qapp.processEvents()

    ring = _pixel(switch, 1)
    track = _track_left(switch) if checked else _track_right(switch)
    page = _pixel(switch, 0, 0)  # a corner of the widget: nothing but the page

    assert contrast_ratio(ring.name(), track.name()) >= 3.0, "the ring is lost against the track"
    assert contrast_ratio(ring.name(), page.name()) >= 3.0, "the ring is lost against the page"


@pytest.mark.parametrize(("mode", "tokens"), _THEME_CASES)
def test_the_focus_ring_is_drawn_in_the_design_systems_ring_colour(qtbot, qapp, mode, tokens):
    switch = _switch(qtbot, qapp, mode=mode)
    set_focus_ring(switch, True)
    qapp.processEvents()
    assert _rgb(_pixel(switch, 1)) == _rgb(QColor(tokens.focus_ring))
    assert _rgb(_pixel(switch, 1)) != _rgb(QColor(tokens.accent_text))  # no longer the accent


def test_the_focus_ring_follows_a_live_theme_change(qtbot, qapp):
    # The manager is kept alive here (the theme changes through it), so its focus filter is live
    # too: focus is moved the way a Tab does, through the filter, rather than by setting the ring
    # by hand, which a late "window activated" focus event would then undo.
    manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    switch = ToggleSwitch("Take breaks")
    qtbot.addWidget(switch)
    switch.show()
    qtbot.waitUntil(switch.hasFocus, timeout=2000)  # the window's first focus, from its activation
    QApplication.sendEvent(switch, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    qapp.processEvents()
    assert _rgb(_pixel(switch, 1)) == _rgb(QColor(LIGHT_TOKENS.focus_ring))

    manager.set_mode(ThemeMode.DARK)
    qapp.processEvents()
    assert _rgb(_pixel(switch, 1)) == _rgb(QColor(DARK_TOKENS.focus_ring))


def test_without_a_stylesheet_the_ring_falls_back_to_the_window_text_colour(qtbot, qapp):
    # A switch in an application that never applied the theme's stylesheet still gets a ring
    # that contrasts with its page: the palette's window text is the same near-black/near-white.
    ThemeManager(qapp, mode=ThemeMode.LIGHT)
    qapp.setStyleSheet("")
    try:
        switch = ToggleSwitch("Take breaks")
        qtbot.addWidget(switch)
        switch.show()
        qtbot.waitExposed(switch)
        assert not switch.focusRingColor.isValid()
        set_focus_ring(switch, True)
        qapp.processEvents()
        assert _rgb(_pixel(switch, 1)) == _rgb(QColor(LIGHT_TOKENS.text))
    finally:
        ThemeManager(qapp, mode=ThemeMode.LIGHT)  # put the app stylesheet back for later tests


# --- layout helpers -------------------------------------------------------------------------


def test_switch_row_lines_the_track_up_with_the_labels_above(qtbot, qapp):
    ThemeManager(qapp)
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(16, 16, 16, 16)
    label = QLabel("Profile")
    switch = ToggleSwitch("Take breaks")
    layout.addWidget(label)
    layout.addLayout(switch_row(switch))
    host.show()
    qtbot.waitExposed(host)
    track_left = switch.mapTo(host, QPoint(0, 0)).x() + _RING_MARGIN
    assert track_left == label.mapTo(host, QPoint(0, 0)).x()


def test_scroll_page_layout_reports_no_height_for_width_to_the_scroll_area(qtbot, qapp):
    host = QWidget()
    qtbot.addWidget(host)
    layout = ScrollPageLayout(host)
    wrapped = QLabel("a long line that wraps " * 10)
    wrapped.setWordWrap(True)
    layout.addWidget(wrapped)
    assert layout.hasHeightForWidth() is False
    assert QVBoxLayout().hasHeightForWidth() is False  # nothing in it yet
    plain = QVBoxLayout()
    plain.addWidget(QLabel("x"))


def test_scroll_page_layout_lets_a_stretchy_item_shrink_before_the_area_scrolls(qtbot, qapp):
    ThemeManager(qapp)
    area = QScrollArea()
    area.setWidgetResizable(True)
    qtbot.addWidget(area)
    content = QWidget()
    area.setWidget(content)
    layout = ScrollPageLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    wrapped = QLabel("a sentence long enough that it wraps onto several lines " * 6)
    wrapped.setWordWrap(True)
    editor = QPlainTextEdit()
    editor.setMinimumHeight(40)
    layout.addWidget(wrapped)
    layout.addWidget(editor, 1)

    # Room for the label at its wrapped height plus the editor's minimum, but well short of the
    # editor's preferred height.
    area.resize(300, 190)
    area.show()
    qtbot.waitExposed(area)
    qapp.processEvents()

    assert editor.sizeHint().height() > editor.height() >= 40
    assert area.verticalScrollBar().maximum() == 0, "scrolled even though squeezing the editor fit"


def test_scroll_page_layout_still_scrolls_when_even_the_minimum_does_not_fit(qtbot, qapp):
    ThemeManager(qapp)
    area = QScrollArea()
    area.setWidgetResizable(True)
    qtbot.addWidget(area)
    content = QWidget()
    area.setWidget(content)
    layout = ScrollPageLayout(content)
    editor = QPlainTextEdit()
    editor.setMinimumHeight(300)
    layout.addWidget(editor)
    area.resize(300, 150)
    area.show()
    qtbot.waitExposed(area)
    qapp.processEvents()
    assert area.verticalScrollBar().maximum() > 0
    assert editor.height() >= 300


@pytest.mark.parametrize("key", [Qt.Key.Key_Space])
def test_space_release_without_press_does_not_toggle(qtbot, qapp, key):
    switch = _switch(qtbot, qapp)
    QTest.keyRelease(switch, key)
    assert switch.isChecked() is False

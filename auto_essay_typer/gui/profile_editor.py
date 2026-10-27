"""A form for every typing-profile setting, generated from the profile's own metadata.

Nothing here names a ``Profile`` field. The controls come from three sources
that already describe the model: the dataclass's type hints (what kind of
value a field holds), ``profiles.FIELD_INFO`` (label, help, unit, bounds,
step) and ``profiles.group_fields()`` (how fields are grouped and ordered). A
twentieth knob added to ``Profile`` therefore shows up here by itself, in the
right group, with the right kind of control.

Two design points worth knowing:

*Exact values.* Spin boxes round to a fixed number of decimals and clamp to a
range, so reading a value back out of one can silently change it (the
built-in "clean" preset has ``revision_rate_per_word = 1/800``, which a
three-decimal spin box would turn into 0.001). The editor therefore keeps its
own exact copy of every value and replaces an entry only when the *user*
edits that control; loading a profile never routes a value through a spin
box. To show an unusual value honestly, the spin box's range and decimals are
widened to fit it rather than clamping it.

*Percent fields.* A few fields (``hesitation_probability`` and friends) are
stored as a 0..1 fraction but their ``FIELD_INFO`` unit is ``"%"``. They are
edited as 0..100 with a ``%`` suffix, and converted back through ``Decimal``
so 7 % is exactly 0.07 rather than 0.07000000000000001.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, get_args, get_origin, get_type_hints

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from auto_essay_typer import profiles
from auto_essay_typer.gui.advanced_common import (
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    ThemedMixin,
    humanize_ms,
    humanize_ms_range,
    spinbox_stylesheet,
)
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.gui.widgets import CollapsibleSection
from auto_essay_typer.profiles import FieldInfo, Profile

KIND_FLOAT = "float"
KIND_INT = "int"
KIND_BOOL = "bool"
KIND_RANGE = "range"

#: Width of the control column in each field row.
CONTROL_WIDTH = 250
#: Most decimals a spin box will show; a longer value is displayed rounded
#: (the exact value is still kept).
MAX_DECIMALS = 6
#: Group for any field a future ``group_fields()`` forgets to place.
_FALLBACK_GROUP = "Other"


# --- field metadata ------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """Everything needed to build and validate the control for one ``Profile`` field."""

    name: str
    info: FieldInfo
    kind: str  # KIND_FLOAT | KIND_INT | KIND_BOOL | KIND_RANGE
    element: type  # float or int; bool for KIND_BOOL
    percent: bool  # stored as a 0..1 fraction, edited as 0..100

    @property
    def scale(self) -> float:
        return 100.0 if self.percent else 1.0


def _spec_for(name: str, annotation: Any) -> FieldSpec:
    info = profiles.FIELD_INFO[name]
    percent = info.unit == "%"
    if get_origin(annotation) is tuple:
        args = get_args(annotation)
        if len(args) == 2 and args[0] is args[1] and args[0] in (int, float):
            return FieldSpec(name, info, KIND_RANGE, args[0], percent)
    elif annotation is bool:
        return FieldSpec(name, info, KIND_BOOL, bool, False)
    elif annotation is int:
        return FieldSpec(name, info, KIND_INT, int, percent)
    elif annotation is float:
        return FieldSpec(name, info, KIND_FLOAT, float, percent)
    raise TypeError(f"the profile editor does not know how to edit {name!r} (type {annotation!r})")


def field_specs() -> list[tuple[str, list[FieldSpec]]]:
    """``(group, specs)`` pairs in display order, covering every ``Profile`` field."""
    hints = get_type_hints(Profile)
    groups = [
        (group, [_spec_for(name, hints[name]) for name in names])
        for group, names in profiles.group_fields()
    ]
    placed = {spec.name for _group, specs in groups for spec in specs}
    stray = [name for name in profiles.field_names() if name not in placed]
    if stray:
        groups.append((_FALLBACK_GROUP, [_spec_for(name, hints[name]) for name in stray]))
    return groups


# --- number conversion -----------------------------------------------------------------


def decimals_needed(value: float, cap: int = MAX_DECIMALS) -> int:
    """The fewest decimals (up to ``cap``) that show ``value`` without visible rounding."""
    magnitude = abs(value)
    if not math.isfinite(magnitude):
        return 0
    for decimals in range(cap + 1):
        if abs(round(magnitude, decimals) - magnitude) <= 1e-9 * max(1.0, magnitude):
            return decimals
    return cap


def to_display(spec: FieldSpec, value: float | int) -> float | int:
    """The number a spin box should show for the stored ``value``."""
    if spec.element is int:
        return int(value)
    if spec.percent:
        try:
            return float(Decimal(repr(float(value))) * 100)
        except (ArithmeticError, ValueError):
            return float(value) * 100.0
    return float(value)


def _from_display(spec: FieldSpec, display: float | int, decimals: int) -> float | int:
    """The stored value for what the user typed into a spin box."""
    if spec.element is int:
        return int(display)
    if spec.percent:
        return float(Decimal(f"{display:.{decimals}f}") / 100)
    return float(display)


# --- validation --------------------------------------------------------------------------


def validate_values(
    values: dict[str, Any], specs: list[FieldSpec]
) -> tuple[dict[str, str], str]:
    """Check ``values``; return ``({field: message}, general_message)``.

    A range needs ``low <= high``. After that the values are handed to
    ``Profile(...)`` so its own ``__post_init__`` checks surface too: a message
    that names a field is attached to that field (reworded with the field's
    label), anything else comes back as the general message.
    """
    errors: dict[str, str] = {}
    for spec in specs:
        if spec.kind == KIND_RANGE:
            low, high = values[spec.name]
            if low > high:
                errors[spec.name] = "Low must not be greater than high"

    general = ""
    try:
        Profile(**values)
    except ValueError as exc:
        message = str(exc)
        owner = _field_named_in(message, specs)
        if owner is not None and owner.name in errors:
            pass  # already explained by the more specific message above
        elif owner is not None:
            errors[owner.name] = message.replace(owner.name, owner.info.label, 1)
        elif not errors:
            general = message
    return errors, general


def _field_named_in(message: str, specs: list[FieldSpec]) -> FieldSpec | None:
    """The field a ``Profile`` error message is about (prefix match first, longest name wins)."""
    by_length = sorted(specs, key=lambda spec: len(spec.name), reverse=True)
    for spec in by_length:
        if message.startswith(spec.name):
            return spec
    for spec in by_length:
        if spec.name in message:
            return spec
    return None


# --- one numeric control -------------------------------------------------------------------


class _NumberControl(QObject):
    """A spin box plus the translation between what it shows and the exact stored value."""

    edited = Signal(object)  # the exact new stored value, after a user edit

    def __init__(self, spec: FieldSpec, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.spin = NoWheelDoubleSpinBox() if spec.element is float else NoWheelSpinBox()
        self.spin.setAccelerated(True)
        self.spin.setKeyboardTracking(True)
        self.spin.setGroupSeparatorShown(True)
        if spec.info.unit:
            self.spin.setSuffix(f" {spec.info.unit}")
        self._loading = False
        self.spin.valueChanged.connect(self._on_value_changed)

    def load(self, value: float | int) -> None:
        """Show ``value``, widening the range/decimals if it would not fit as configured.

        Never emits :attr:`edited`: this is a programmatic load, not a user edit.
        """
        spec, info = self.spec, self.spec.info
        scale = spec.scale
        low, high, step = info.minimum * scale, info.maximum * scale, info.step * scale
        display = to_display(spec, value)
        if not math.isfinite(display):
            display = low
        self._loading = True
        try:
            if spec.element is float:
                self.spin.setDecimals(max(decimals_needed(step), decimals_needed(display)))
                self.spin.setRange(min(low, display), max(high, display))
                self.spin.setSingleStep(step)
                self.spin.setValue(display)
            else:
                self.spin.setRange(math.floor(min(low, display)), math.ceil(max(high, display)))
                self.spin.setSingleStep(max(1, round(step)))
                self.spin.setValue(int(display))
        finally:
            self._loading = False

    def _on_value_changed(self, display: float | int) -> None:
        if self._loading:
            return
        decimals = self.spin.decimals() if isinstance(self.spin, NoWheelDoubleSpinBox) else 0
        self.edited.emit(_from_display(self.spec, display, decimals))


class RangeEdit(QWidget):
    """Two spin boxes -- low and high -- joined by "to"."""

    def __init__(self, low: QWidget, high: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.low = low
        self.high = high
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(low, 1)
        to_label = QLabel("to")
        to_label.setObjectName("MutedLabel")
        layout.addWidget(to_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(high, 1)


# --- one field row ---------------------------------------------------------------------------


class FieldRow(QWidget):
    """Label and help on the left; the control, a hint and any error on the right."""

    valueEdited = Signal(str, object)  # field name, exact new value

    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        info = spec.info
        self._value: Any = None
        self._numbers: list[_NumberControl] = []
        self.check_box: QCheckBox | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.title_label = QLabel(info.label)
        self.title_label.setObjectName("FieldTitle")
        self.title_label.setToolTip(info.help)
        self.title_label.setWordWrap(True)
        left.addWidget(self.title_label)
        self.help_label = QLabel(info.help)
        self.help_label.setObjectName("MutedLabel")
        self.help_label.setWordWrap(True)
        left.addWidget(self.help_label)
        left.addStretch(1)
        outer.addLayout(left, 1)

        right = QWidget()
        right.setFixedWidth(CONTROL_WIDTH)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self.control = self._build_control()
        self.control.setToolTip(info.help)
        right_layout.addWidget(self.control)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("MutedLabel")
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(False)
        right_layout.addWidget(self.hint_label)
        self.error_label = QLabel()
        self.error_label.setObjectName("FieldError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        right_layout.addWidget(self.error_label)
        right_layout.addStretch(1)
        outer.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)

    # -- construction -----------------------------------------------------------------------

    def _build_control(self) -> QWidget:
        spec, label = self.spec, self.spec.info.label
        if spec.kind == KIND_BOOL:
            box = QCheckBox()
            box.setObjectName("ToggleSwitch")
            box.setAccessibleName(label)
            box.toggled.connect(self._on_toggled)
            self.check_box = box
            return box

        if spec.kind == KIND_RANGE:
            low, high = _NumberControl(spec, self), _NumberControl(spec, self)
            low.spin.setAccessibleName(f"{label} (low)")
            high.spin.setAccessibleName(f"{label} (high)")
            low.edited.connect(lambda new: self._on_range_edited(0, new))
            high.edited.connect(lambda new: self._on_range_edited(1, new))
            self._numbers = [low, high]
            return RangeEdit(low.spin, high.spin)

        number = _NumberControl(spec, self)
        number.spin.setAccessibleName(label)
        number.edited.connect(self._on_single_edited)
        self._numbers = [number]
        return number.spin

    # -- value ----------------------------------------------------------------------------------

    def value(self) -> Any:
        """The exact stored value (what ``Profile`` will receive)."""
        return self._value

    def set_value(self, value: Any) -> None:
        """Show ``value`` without treating it as a user edit."""
        self._value = value
        if self.spec.kind == KIND_BOOL:
            assert self.check_box is not None
            self.check_box.blockSignals(True)
            self.check_box.setChecked(bool(value))
            self.check_box.blockSignals(False)
        elif self.spec.kind == KIND_RANGE:
            self._numbers[0].load(value[0])
            self._numbers[1].load(value[1])
        else:
            self._numbers[0].load(value)
        self._refresh_hint()

    def spin_boxes(self) -> list[QWidget]:
        return [number.spin for number in self._numbers]

    def _on_toggled(self, checked: bool) -> None:
        self._value = bool(checked)
        self.valueEdited.emit(self.spec.name, self._value)

    def _on_single_edited(self, new: Any) -> None:
        self._value = new
        self._refresh_hint()
        self.valueEdited.emit(self.spec.name, new)

    def _on_range_edited(self, index: int, new: Any) -> None:
        low, high = self._value
        self._value = (new, high) if index == 0 else (low, new)
        self._refresh_hint()
        self.valueEdited.emit(self.spec.name, self._value)

    # -- hints and errors ------------------------------------------------------------------------

    def _refresh_hint(self) -> None:
        spec, info = self.spec, self.spec.info
        parts: list[str] = []
        if info.unit == "ms":
            if spec.kind == KIND_RANGE:
                text = humanize_ms_range(*self._value)
            elif spec.kind in (KIND_FLOAT, KIND_INT):
                text = humanize_ms(self._value)
            else:
                text = ""
            if text:
                parts.append(text[0].upper() + text[1:])
        if self._outside_usual_range():
            parts.append(
                f"Outside the usual range ({info.minimum * spec.scale:g} to "
                f"{info.maximum * spec.scale:g}); kept as set"
            )
        self.hint_label.setText(" - ".join(parts))
        self.hint_label.setVisible(bool(parts))

    def _outside_usual_range(self) -> bool:
        spec, info = self.spec, self.spec.info
        if spec.kind == KIND_BOOL:
            return False
        values = self._value if spec.kind == KIND_RANGE else (self._value,)
        return any(not (info.minimum <= v <= info.maximum) for v in values)

    def set_error(self, message: str | None) -> None:
        """Show (or clear) the error under the control and mark the spin boxes invalid."""
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))
        for spin in self.spin_boxes():
            spin.setProperty("invalid", bool(message))
            spin.style().unpolish(spin)
            spin.style().polish(spin)


# --- the editor -----------------------------------------------------------------------------------


class ProfileEditor(QWidget, ThemedMixin):
    """Collapsible groups of generated field rows, with exact values and live validation.

    ``stateChanged`` fires after anything that could change what the buttons
    around the editor should do: a user edit or a programmatic load.
    """

    stateChanged = Signal()

    def __init__(
        self, theme_manager: ThemeManager | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProfileEditor")
        groups = field_specs()
        self._specs: list[FieldSpec] = [spec for _group, specs in groups for spec in specs]
        self._groups: dict[str, list[FieldSpec]] = {group: specs for group, specs in groups}
        self._values: dict[str, Any] = {}
        self._baseline: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._general_error = ""

        self.rows: dict[str, FieldRow] = {}
        self.field_widgets: dict[str, QWidget] = {}
        self.group_sections: dict[str, CollapsibleSection] = {}
        self._badges: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for index, (group, specs) in enumerate(groups):
            section = CollapsibleSection(group, expanded=index == 0, theme_manager=theme_manager)
            self._badges[group] = self._attach_badge(section)
            for position, spec in enumerate(specs):
                if position:
                    section.body_layout.addWidget(self._divider())
                row = FieldRow(spec)
                row.valueEdited.connect(self._on_row_edited)
                section.body_layout.addWidget(row)
                self.rows[spec.name] = row
                self.field_widgets[spec.name] = row.control
            self.group_sections[group] = section
            layout.addWidget(section)

        # Start from the standard defaults so a standalone editor is never blank.
        self.load_profile(Profile())
        self._bind_theme(theme_manager)

    # -- construction helpers -------------------------------------------------------------------

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("FieldDivider")
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _attach_badge(section: CollapsibleSection) -> QLabel:
        """A small right-aligned status label inside a section's header button."""
        layout = QHBoxLayout(section.header_button)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.addStretch(1)
        badge = QLabel()
        badge.setObjectName("GroupBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge.setVisible(False)
        layout.addWidget(badge)
        return badge

    # -- loading and reading ----------------------------------------------------------------------

    def load_profile(self, profile: Profile, *, baseline: bool = True) -> None:
        """Fill every field from ``profile``.

        With ``baseline=True`` (the default) this is the saved state the editor
        compares against for "unsaved changes"; ``baseline=False`` keeps the old
        baseline, so loading e.g. a built-in's defaults into a saved profile's
        editor leaves it dirty exactly when the two differ.
        """
        self._values = {spec.name: getattr(profile, spec.name) for spec in self._specs}
        if baseline:
            self._baseline = dict(self._values)
        for name, row in self.rows.items():
            row.set_value(self._values[name])
        self._revalidate()
        self.stateChanged.emit()

    def mark_saved(self) -> None:
        """Treat the editor's current values as the saved state (call after a successful save)."""
        self._baseline = dict(self._values)
        self._update_badges()
        self.stateChanged.emit()

    def current_profile(self) -> Profile:
        """The profile the editor currently describes; ``ValueError`` if any field is invalid."""
        if self._errors or self._general_error:
            first = next(iter(self._errors.values()), self._general_error)
            raise ValueError(first)
        return Profile(**self._values)

    def values(self) -> dict[str, Any]:
        return dict(self._values)

    # -- state ------------------------------------------------------------------------------------------

    def errors(self) -> dict[str, str]:
        """Field name -> message for every invalid field (a copy)."""
        return dict(self._errors)

    def general_error(self) -> str:
        """A ``Profile`` error that could not be attributed to one field, or ``""``."""
        return self._general_error

    def is_valid(self) -> bool:
        return not self._errors and not self._general_error

    def error_count(self) -> int:
        return len(self._errors) + (1 if self._general_error else 0)

    def error_summary(self) -> str:
        """``"Fix 2 settings before saving"``, or ``""`` when everything is valid."""
        count = self.error_count()
        if not count:
            return ""
        return f"Fix {count} setting{'s' if count != 1 else ''} before saving"

    def is_dirty(self) -> bool:
        return self._values != self._baseline

    def changed_fields(self) -> list[str]:
        return [name for name in self._values if self._values[name] != self._baseline.get(name)]

    # -- section controls ------------------------------------------------------------------------------------

    def expand_all(self) -> None:
        for section in self.group_sections.values():
            section.set_expanded(True)

    def collapse_all(self) -> None:
        for section in self.group_sections.values():
            section.set_expanded(False)

    # -- internals ------------------------------------------------------------------------------------------------

    def _on_row_edited(self, name: str, value: Any) -> None:
        self._values[name] = value
        self._revalidate()
        self.stateChanged.emit()

    def _revalidate(self) -> None:
        self._errors, self._general_error = validate_values(self._values, self._specs)
        for name, row in self.rows.items():
            row.set_error(self._errors.get(name))
        self._update_badges()

    def _update_badges(self) -> None:
        changed = set(self.changed_fields())
        for group, specs in self._groups.items():
            names = [spec.name for spec in specs]
            problems = sum(1 for name in names if name in self._errors)
            edits = sum(1 for name in names if name in changed)
            badge = self._badges[group]
            if problems:
                badge.setText(f"{problems} issue{'s' if problems != 1 else ''}")
                tone = "danger"
                # An error inside a folded group would otherwise be invisible.
                self.group_sections[group].set_expanded(True)
            elif edits:
                badge.setText(f"{edits} changed")
                tone = "accent"
            else:
                badge.setText("")
                tone = ""
            badge.setProperty("tone", tone)
            badge.style().unpolish(badge)
            badge.style().polish(badge)
            badge.setVisible(bool(badge.text()))

    # -- theming ----------------------------------------------------------------------------------------------------

    def _restyle(self) -> None:
        t = self.tokens
        self.setStyleSheet(
            spinbox_stylesheet(t)
            + f"""
            QLabel#FieldTitle {{ font-size: 13px; font-weight: 600; }}
            QLabel#FieldError {{ color: {t.danger}; font-size: 12px; font-weight: 600; }}
            QLabel#GroupBadge {{ font-size: 12px; font-weight: 600; background: transparent; }}
            QLabel#GroupBadge[tone="danger"] {{ color: {t.danger}; }}
            QLabel#GroupBadge[tone="accent"] {{ color: {t.accent}; }}
            QFrame#FieldDivider {{ background: {t.border}; border: none; }}
            """
        )

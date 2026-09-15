"""Scrollable ttk editor for every field on :class:`auto_essay_typer.profiles.Profile`.

The field list is never hardcoded: it is derived at runtime from
``dataclasses.fields(Profile)``, so a field added to (or removed from) the
dataclass appears in (or disappears from) this panel automatically, with no
change needed here. Only the *section headings* are named explicitly, purely
for layout grouping -- they mirror the comment groups already present in
``profiles.py`` (Speed / Thinking pauses / Mistakes / Revision / Session
breaks). A field that does not start a new named section simply joins
whichever section precedes it, so nothing is ever silently dropped from the
panel even if a new field's name doesn't match a boundary below.
"""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import ttk
from typing import get_origin

from auto_essay_typer.profiles import Profile, coerce_value

# (name of the field that starts a section, section label) -- ordered to
# match the dataclass's own declaration order in profiles.py. Note the
# order within each pair: dict(_SECTION_BOUNDARIES) below must come out
# keyed by field name (what _sectioned_fields looks up per field), not by
# label.
_SECTION_BOUNDARIES: tuple[tuple[str, str], ...] = (
    ("wpm", "Speed"),
    ("sentence_pause_ms", "Thinking pauses"),
    ("typo_rate_per_100", "Mistakes"),
    ("revision_rate_per_word", "Revision"),
    ("session_breaks", "Session breaks"),
    ("format_shortcut_ms", "Formatting"),
)


def _sectioned_fields() -> list[tuple[str, list[dataclasses.Field]]]:
    """Every Profile field, grouped into labelled sections, in declared order."""
    label_by_field_name = dict(_SECTION_BOUNDARIES)
    sections: list[tuple[str, list[dataclasses.Field]]] = []
    current_label = _SECTION_BOUNDARIES[0][1]
    current_fields: list[dataclasses.Field] = []
    for f in dataclasses.fields(Profile):
        if f.name in label_by_field_name:
            if current_fields:
                sections.append((current_label, current_fields))
            current_label = label_by_field_name[f.name]
            current_fields = []
        current_fields.append(f)
    if current_fields:
        sections.append((current_label, current_fields))
    return sections


def _is_tuple_field(f: dataclasses.Field) -> bool:
    return get_origin(f.type) is tuple


class SettingsPanel(ttk.Frame):
    """Scrollable editor for all Profile fields, grouped into labelled sections.

    - ``bool`` fields get a ``ttk.Checkbutton``.
    - ``tuple[float, float]`` / ``tuple[int, int]`` fields get two side by
      side ``ttk.Entry`` widgets, labelled low/high.
    - Everything else (``float``, ``int``) gets a single ``ttk.Entry``.

    Call :meth:`load` to populate widgets from a Profile, and
    :meth:`to_profile` to read them back out.
    """

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self._scalar_vars: dict[str, tk.StringVar] = {}
        self._bool_vars: dict[str, tk.BooleanVar] = {}
        self._tuple_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        self._build()

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        interior = ttk.Frame(canvas)

        interior.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_window = canvas.create_window((0, 0), window=interior, anchor="nw")
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width)
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event: tk.Event) -> None:
            delta = event.delta
            step = -1 if delta > 0 else 1
            canvas.yview_scroll(step, "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        interior.bind("<MouseWheel>", _on_mousewheel)

        for label, fields in _sectioned_fields():
            section = ttk.LabelFrame(interior, text=label, padding=8)
            section.pack(fill="x", expand=True, padx=6, pady=(4, 2))
            section.columnconfigure(1, weight=1)
            for row, f in enumerate(fields):
                self._build_field_row(section, row, f)

    def _build_field_row(self, parent: ttk.Frame, row: int, f: dataclasses.Field) -> None:
        ttk.Label(parent, text=f.name).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=2
        )
        if f.type is bool:
            var = tk.BooleanVar(value=bool(f.default))
            ttk.Checkbutton(parent, variable=var).grid(row=row, column=1, sticky="w")
            self._bool_vars[f.name] = var
        elif _is_tuple_field(f):
            low_default, high_default = f.default
            low_var = tk.StringVar(value=str(low_default))
            high_var = tk.StringVar(value=str(high_default))
            cell = ttk.Frame(parent)
            cell.grid(row=row, column=1, sticky="w")
            ttk.Label(cell, text="low").pack(side="left")
            ttk.Entry(cell, textvariable=low_var, width=10).pack(side="left", padx=(2, 8))
            ttk.Label(cell, text="high").pack(side="left")
            ttk.Entry(cell, textvariable=high_var, width=10).pack(side="left", padx=(2, 0))
            self._tuple_vars[f.name] = (low_var, high_var)
        else:
            var = tk.StringVar(value=str(f.default))
            ttk.Entry(parent, textvariable=var, width=14).grid(row=row, column=1, sticky="w")
            self._scalar_vars[f.name] = var

    # -- data -------------------------------------------------------------

    def load(self, profile: Profile) -> None:
        """Populate every widget from ``profile``."""
        for name, var in self._scalar_vars.items():
            var.set(str(getattr(profile, name)))
        for name, var in self._bool_vars.items():
            var.set(bool(getattr(profile, name)))
        for name, (low_var, high_var) in self._tuple_vars.items():
            low, high = getattr(profile, name)
            low_var.set(str(low))
            high_var.set(str(high))

    def set_value(self, name: str, value: object) -> None:
        """Update a single field's widget(s) directly.

        Used by the app to mirror a quick control (e.g. the WPM slider) into
        the corresponding Advanced-panel widget, so the two never disagree.
        """
        if name in self._scalar_vars:
            self._scalar_vars[name].set(str(value))
        elif name in self._bool_vars:
            self._bool_vars[name].set(bool(value))
        elif name in self._tuple_vars:
            low, high = value  # type: ignore[misc]
            low_var, high_var = self._tuple_vars[name]
            low_var.set(str(low))
            high_var.set(str(high))
        else:
            raise KeyError(f"unknown Profile field {name!r}")

    def to_profile(self, base: Profile) -> Profile:
        """Build a new Profile from the current widget contents, layered onto ``base``.

        Every field this panel exposes is read from its widget and applied
        as an override on top of ``base`` (so any field the panel doesn't
        know about -- there shouldn't be one, but ``base`` is the safety net
        -- keeps its value from ``base``). Raises ``ValueError`` naming the
        offending field if any widget holds text that cannot be parsed as
        that field's declared type.
        """
        changes: dict[str, object] = {}
        for name, var in self._scalar_vars.items():
            changes[name] = coerce_value(name, var.get())
        for name, var in self._bool_vars.items():
            changes[name] = coerce_value(name, bool(var.get()))
        for name, (low_var, high_var) in self._tuple_vars.items():
            changes[name] = coerce_value(name, f"{low_var.get()},{high_var.get()}")
        return base.with_overrides(**changes)

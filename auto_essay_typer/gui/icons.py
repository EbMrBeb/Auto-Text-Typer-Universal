"""Simple line icons, hand-drawn as inline SVG and rendered through QtSvg.

No external icon pack and no emoji, per the design spec: every icon here is
plain geometry (lines, circles, simple paths) on a 24x24 grid, defined
in-line so there is nothing to license or bundle beyond this file. Icons are
single-color; ``{color}`` in the SVG source is substituted with whatever the
caller asks for (usually a theme token, so an icon recolors correctly across
light/dark and active/inactive nav states).

``QtSvg`` ships with ``PySide6-Essentials`` (verified against the version
this project pins), so no ``QPainter``-only fallback is implemented here.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)
_FILL_TEMPLATE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}" stroke="none">{body}</svg>'

#: icon name -> (SVG body markup, filled-shape?). Stroke icons (the common
#: case) are outlines; a filled icon (play/pause/stop/break) is a solid glyph.
_ICONS: dict[str, tuple[str, bool]] = {
    "type": ('<path d="M8 4h8M12 4v16M8 20h8"/>', False),
    "settings": (
        '<line x1="3" y1="6" x2="21" y2="6"/><circle cx="8" cy="6" r="2"/>'
        '<line x1="3" y1="12" x2="21" y2="12"/><circle cx="16" cy="12" r="2"/>'
        '<line x1="3" y1="18" x2="21" y2="18"/><circle cx="10" cy="18" r="2"/>',
        False,
    ),
    "help": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M9.3 9.6a2.7 2.7 0 1 1 3.8 2.5c-.8.4-1.1.9-1.1 1.9"/>'
        '<line x1="12" y1="17" x2="12" y2="17.1"/>',
        False,
    ),
    "check-circle": ('<circle cx="12" cy="12" r="9"/><path d="M8 12.3l2.6 2.6L16 9"/>', False),
    "alert-triangle": (
        '<path d="M12 3.5 L21.5 20 L2.5 20 Z"/>'
        '<line x1="12" y1="9.5" x2="12" y2="14"/><line x1="12" y1="17" x2="12" y2="17.1"/>',
        False,
    ),
    "play": ('<path d="M8 5.5 L19 12 L8 18.5 Z"/>', True),
    "pause": (
        '<rect x="7" y="5" width="4" height="14" rx="1"/>'
        '<rect x="13" y="5" width="4" height="14" rx="1"/>',
        True,
    ),
    "stop": ('<rect x="6" y="6" width="12" height="12" rx="2"/>', True),
    "close": ('<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>', False),
    "file": (
        '<path d="M7 3 L14 3 L18 7 L18 21 L7 21 Z"/><path d="M14 3 L14 7 L18 7"/>'
        '<line x1="9.5" y1="13" x2="15" y2="13"/><line x1="9.5" y1="17" x2="15" y2="17"/>',
        False,
    ),
    "upload": (
        '<path d="M12 15V4"/><path d="M7 8.5 12 4 17 8.5"/>'
        '<path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
        False,
    ),
    "typo": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
        False,
    ),
    "revision": ('<path d="M4.5 9a8 8 0 1 1 1.8 8.9"/><path d="M4 4v5h5"/>', False),
    "pause-event": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="12" y1="7" x2="12" y2="12.3"/><line x1="12" y1="12.3" x2="15.3" y2="14.2"/>',
        False,
    ),
    "break": ('<path d="M20 14.5A8.5 8.5 0 1 1 11.6 5.1 7 7 0 0 0 20 14.5Z"/>', True),
    "chevron-down": ('<path d="M6 9 L12 15 L18 9"/>', False),
}


def icon_names() -> tuple[str, ...]:
    return tuple(sorted(_ICONS))


def _svg_source(name: str, color: str, stroke_width: float) -> str:
    entry = _ICONS.get(name)
    if entry is None:
        raise KeyError(f"unknown icon {name!r}; available icons are {icon_names()}")
    body, filled = entry
    template = _FILL_TEMPLATE if filled else _STROKE_TEMPLATE
    return template.format(color=color, stroke_width=stroke_width, body=body)


def pixmap(name: str, color: str, size: int = 20, *, stroke_width: float = 2.0) -> QPixmap:
    """Render icon ``name`` at ``size`` x ``size`` device-independent pixels, tinted ``color``."""
    source = _svg_source(name, color, stroke_width)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    px = QPixmap(QSize(size, size))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return px


def icon(name: str, color: str, size: int = 20, *, stroke_width: float = 2.0) -> QIcon:
    """A :class:`QIcon` for icon ``name``, tinted ``color`` (a ``"#RRGGBB"`` string)."""
    return QIcon(pixmap(name, color, size, stroke_width=stroke_width))

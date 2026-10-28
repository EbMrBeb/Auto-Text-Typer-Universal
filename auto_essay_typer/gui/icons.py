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

import atexit
import math
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
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
    # Angle brackets with a slash: the Advanced page (profile fields, shortcut overrides).
    "code": ('<path d="M8 7 L3 12 L8 17"/><path d="M16 7 L21 12 L16 17"/><path d="M14 4.5 L10 19.5"/>', False),
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
    "check": ('<path d="M5 12.5 L10 17.5 L19 7"/>', False),
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


def _device_pixel_ratio() -> int:
    """Render scale for icon pixmaps: the primary screen's ratio, rounded up, at least 2.

    A pixmap rendered at 1x and then drawn on a Retina/HiDPI screen is blurry;
    rendering at 2x or more and tagging the pixmap with its ratio keeps nav
    icons crisp everywhere and costs nothing on a 1x screen.
    """
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return max(2, math.ceil(ratio))


def _render(name: str, color: str, pixel_size: int, stroke_width: float) -> QImage:
    """Icon ``name`` painted onto a transparent ``pixel_size`` square QImage.

    QImage (unlike QPixmap) needs no QGuiApplication, so :func:`themed_file`
    works even where only the stylesheet is being built, as in unit tests.
    """
    source = _svg_source(name, color, stroke_width)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    image = QImage(QSize(pixel_size, pixel_size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return image


def pixmap(name: str, color: str, size: int = 20, *, stroke_width: float = 2.0) -> QPixmap:
    """Render icon ``name`` at ``size`` x ``size`` device-independent pixels, tinted ``color``."""
    ratio = _device_pixel_ratio()
    px = QPixmap.fromImage(_render(name, color, size * ratio, stroke_width))
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, color: str, size: int = 20, *, stroke_width: float = 2.0) -> QIcon:
    """A :class:`QIcon` for icon ``name``, tinted ``color`` (a ``"#RRGGBB"`` string)."""
    return QIcon(pixmap(name, color, size, stroke_width=stroke_width))


_file_cache_dir: Path | None = None
_file_cache: dict[tuple[str, str, int, int, float], str] = {}


def _cache_dir() -> Path:
    """A per-process scratch directory for :func:`themed_file`, removed at exit."""
    global _file_cache_dir
    if _file_cache_dir is None or not _file_cache_dir.is_dir():
        directory = Path(tempfile.mkdtemp(prefix="auto_text_typer_icons_"))
        atexit.register(shutil.rmtree, directory, True)
        _file_cache_dir = directory
        _file_cache.clear()
    return _file_cache_dir


def themed_file(
    name: str, color: str, size: int = 12, *, scale: int = 3, stroke_width: float = 2.0
) -> str:
    """Path of a PNG of icon ``name`` in ``color``, for a stylesheet ``url(...)``.

    Qt stylesheets can only draw an image from a file (``image: url(...)``), so
    the icon is rendered once per (name, color, size) into a scratch directory
    that lives as long as the process. The PNG is drawn at ``scale`` times its
    logical ``size``; give the stylesheet rule an explicit ``width``/``height``
    of ``size`` and Qt scales it down, which stays sharp at 1x, 2x and 3x
    (a 1x PNG shown on a Retina screen would be blurry). Returned with forward
    slashes, which is what ``url()`` wants on Windows too.
    """
    directory = _cache_dir()
    key = (name, color.upper(), size, scale, stroke_width)
    cached = _file_cache.get(key)
    if cached is not None and Path(cached).is_file():
        return cached
    filename = f"{name}-{color.lstrip('#').upper()}-{size}x{scale}-{stroke_width:g}.png"
    path = directory / filename
    _render(name, color, size * scale, stroke_width).save(str(path), "PNG")
    _file_cache[key] = path.as_posix()
    return _file_cache[key]

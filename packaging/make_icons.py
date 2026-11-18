#!/usr/bin/env python3
"""Generate Auto Text Typer's app and tray icons.

Everything is drawn with Pillow at a supersampled resolution (4x) and
downsampled with LANCZOS resampling for smooth, anti-aliased edges, rather
than hand-authoring bitmaps at each target size. Run directly:

    .venv/bin/python packaging/make_icons.py

Writes, relative to the repo root (this script's parent directory's parent):

    assets/icon.png                 1024x1024 master
    assets/icon-256.png             256x256
    assets/icon.ico                 16/24/32/48/64/128/256 multi-resolution
    assets/icon.icns                macOS multi-resolution (via iconutil;
                                     skipped with a warning off Darwin or if
                                     iconutil is not on PATH)
    assets/tray-icon.png            64x64 colour tray/notification-area glyph
    assets/tray-icon-template.png   64x64 macOS "template" image -- a black
                                     glyph on full transparency. AppKit
                                     recolours template images itself for the
                                     active light/dark menu bar and ignores
                                     any RGB the pixels carry, so this MUST
                                     stay black-or-transparent only.

The design: a rounded-square tile with a diagonal deep-indigo-to-violet
gradient, a bold white "T" glyph, and a small accent-coloured text-cursor
bar immediately after it (evoking a caret blinking right after a typed
letter). Kept deliberately simple -- one glyph, one accent shape, no fine
detail -- so it still reads at 16x16.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"

# Supersampling factor: every size is drawn at SIZE * SS and downsampled,
# so edges anti-alias cleanly instead of being drawn jagged at 1x and
# smoothed after the fact.
SS = 4

INDIGO = (36, 27, 94)        # deep indigo -- gradient start (top-left)
VIOLET = (124, 58, 237)      # violet -- gradient end (bottom-right)
WHITE = (255, 255, 255)
ACCENT = (255, 176, 32)      # warm amber -- the cursor accent

# Ordered by preference; the first that exists on disk wins. Arial Bold
# ships with every macOS install (Supplemental fonts), which is all this
# script needs to support since icons are generated once here and the PNG
# outputs are what get committed and shipped.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    raise RuntimeError(
        "No usable bold TrueType font found; tried: "
        + ", ".join(FONT_CANDIDATES)
    )


def _diagonal_gradient(
    size: int, c0: tuple[int, int, int], c1: tuple[int, int, int]
) -> Image.Image:
    """A top-left (``c0``) -> bottom-right (``c1``) linear gradient.

    The mask value at (x, y) depends only on x + y, so it is built from a
    single 1-D lookup table sliced once per row -- O(size) Python-level
    work instead of a size x size double loop, which matters at a 4096x4096
    supersampled canvas.
    """
    denom = 2 * (size - 1) or 1
    lut = bytes(min(255, (i * 255) // denom) for i in range(2 * size - 1))
    data = b"".join(lut[y : y + size] for y in range(size))
    mask = Image.frombytes("L", (size, size), data)

    base = Image.new("RGB", (size, size), c0)
    top_layer = Image.new("RGB", (size, size), c1)
    return Image.composite(top_layer, base, mask)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _base_tile(size: int) -> Image.Image:
    """The rounded-square gradient tile, ``size`` x ``size``, RGBA."""
    gradient = _diagonal_gradient(size, INDIGO, VIOLET).convert("RGBA")
    radius = int(size * 0.22)
    mask = _rounded_mask(size, radius)

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(gradient, (0, 0), mask)

    # A soft, subtle highlight in the upper-left for a touch of depth --
    # low alpha and heavily blurred, clipped to the tile's own rounded-rect
    # mask so it can never bleed past the tile edge.
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    hd.ellipse(
        [-size * 0.35, -size * 0.35, size * 0.75, size * 0.75],
        fill=(255, 255, 255, 40),
    )
    highlight = highlight.filter(ImageFilter.GaussianBlur(size * 0.05))
    r, g, b, a = highlight.split()
    a = ImageChops.multiply(a, mask)
    highlight = Image.merge("RGBA", (r, g, b, a))
    tile.alpha_composite(highlight)

    return tile


def _glyph_and_cursor(size: int, glyph_color: tuple[int, int, int], cursor_color: tuple[int, int, int]) -> Image.Image:
    """The 'T' glyph plus its accent cursor bar, on full transparency."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    font = _load_bold_font(int(size * 0.62))
    glyph = "T"
    bbox = draw.textbbox((0, 0), glyph, font=font)
    gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]

    cursor_w = max(1, int(size * 0.055))
    cursor_gap = int(size * 0.05)
    cursor_h = int(gh * 1.05)

    group_w = gw + cursor_gap + cursor_w
    start_x = (size - group_w) / 2 - bbox[0]
    text_y = (size - gh) / 2 - bbox[1]
    draw.text((start_x, text_y), glyph, font=font, fill=glyph_color)

    cursor_x0 = start_x + bbox[0] + gw + cursor_gap
    cursor_y0 = (size - cursor_h) / 2
    draw.rounded_rectangle(
        [cursor_x0, cursor_y0, cursor_x0 + cursor_w, cursor_y0 + cursor_h],
        radius=cursor_w / 2,
        fill=cursor_color,
    )
    return layer


def render_icon(size: int) -> Image.Image:
    """The full-colour app icon at ``size`` x ``size``, anti-aliased."""
    canvas = size * SS
    tile = _base_tile(canvas)
    tile.alpha_composite(_glyph_and_cursor(canvas, WHITE, ACCENT))
    return tile.resize((size, size), Image.LANCZOS)


def render_template(size: int) -> Image.Image:
    """The macOS 'template' glyph at ``size`` x ``size``: black on transparent.

    No background tile -- template images are a pure shape mask. AppKit
    reads only the alpha channel and recolours the shape itself for the
    active menu bar appearance, discarding whatever RGB is stored here, so
    black is just a sane, visible choice while editing/previewing the PNG.
    """
    canvas = size * SS
    layer = _glyph_and_cursor(canvas, (0, 0, 0), (0, 0, 0))
    return layer.resize((size, size), Image.LANCZOS)


def render_tray(size: int) -> Image.Image:
    """The colour tray/notification-area icon: same design as the app icon,
    simplified to skip the (imperceptible at this size) soft highlight so
    the whole thing renders a bit faster and a bit crisper."""
    canvas = size * SS
    gradient = _diagonal_gradient(canvas, INDIGO, VIOLET).convert("RGBA")
    mask = _rounded_mask(canvas, int(canvas * 0.22))
    tile = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    tile.paste(gradient, (0, 0), mask)
    tile.alpha_composite(_glyph_and_cursor(canvas, WHITE, ACCENT))
    return tile.resize((size, size), Image.LANCZOS)


def write_ico(path: Path, sizes: list[int]) -> None:
    images = [render_icon(s) for s in sizes]
    images[-1].save(
        path,
        format="ICO",
        sizes=[(im.width, im.height) for im in images],
        append_images=images[:-1],
    )


def write_icns(path: Path) -> None:
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("WARNING: skipping icon.icns -- iconutil is only available on macOS.")
        return

    # Apple's iconset naming convention: base size plus an "@2x" companion
    # at double the pixel size for each named slot.
    slots = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    cache: dict[int, Image.Image] = {}
    with tempfile.TemporaryDirectory() as tmp:
        iconset_dir = Path(tmp) / "icon.iconset"
        iconset_dir.mkdir()
        for filename, px in slots.items():
            if px not in cache:
                cache[px] = render_icon(px)
            cache[px].save(iconset_dir / filename)

        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(path)],
            check=True,
        )


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)

    master = render_icon(1024)
    master.save(ASSETS / "icon.png")
    master.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon-256.png")
    print(f"wrote {ASSETS / 'icon.png'} and {ASSETS / 'icon-256.png'}")

    write_ico(ASSETS / "icon.ico", [16, 24, 32, 48, 64, 128, 256])
    print(f"wrote {ASSETS / 'icon.ico'}")

    write_icns(ASSETS / "icon.icns")
    if (ASSETS / "icon.icns").exists():
        print(f"wrote {ASSETS / 'icon.icns'}")

    render_tray(64).save(ASSETS / "tray-icon.png")
    print(f"wrote {ASSETS / 'tray-icon.png'}")

    render_template(64).save(ASSETS / "tray-icon-template.png")
    print(f"wrote {ASSETS / 'tray-icon-template.png'}")

    # Small previews for a quick by-eye legibility check at the hardest
    # (smallest) size, alongside the full-resolution master.
    preview_dir = ASSETS / "_preview"
    preview_dir.mkdir(exist_ok=True)
    render_icon(16).resize((256, 256), Image.NEAREST).save(preview_dir / "icon-16-scaled.png")
    print(f"wrote {preview_dir / 'icon-16-scaled.png'} (16x16 art, scaled up 16x with no smoothing for inspection)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

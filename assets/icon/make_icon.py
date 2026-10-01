"""Generate the TokenWatch application icon (tokenwatch.ico + tokenwatch.png).

The icon mirrors the avatar mode: a dark rounded tile, a ring made of three arcs
(green, amber and red, like the avatar's health arcs) and the lightning bolt. Small sizes use a simplified
drawing (thicker ring, no inner disc) so they stay readable at 16px.

Usage:  python assets/icon/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent
BASE = 1024  # drawn at this size, then downscaled with LANCZOS
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

TILE = "#0d0e17"
TILE_EDGE = "#1f2233"
TRACK = "#1b1c2b"
CORE = "#141522"
CORE_EDGE = "#2a2c42"
BOLT = "#fbbf24"
BOLT_GLOW = "#f59e0b"
# Same angles and health colors as the avatar: green (right), amber (bottom), red (left)
ARCS = (("#34d399", 10, 110), ("#fbbf24", 130, 230), ("#f87171", 250, 350))
# Bolt polygon from the avatar SVG (72x72 box), centered on (36, 36)
BOLT_POINTS = ((37, 23), (28, 36), (35, 36), (32, 49), (44, 34), (37, 34))


def _arc_with_caps(draw: ImageDraw.ImageDraw, center: float, radius: float, width: float,
                   start: float, end: float, color: str) -> None:
    # PIL strokes inward from the box edge, so grow the box to center the stroke on `radius`
    outer = radius + width / 2
    box = (center - outer, center - outer, center + outer, center + outer)
    # Avatar angles run clockwise from 12 o'clock; PIL's start at 3 o'clock
    draw.arc(box, start - 90, end - 90, fill=color, width=round(width))
    for angle in (start, end):
        rad = math.radians(angle - 90)
        cx = center + radius * math.cos(rad)
        cy = center + radius * math.sin(rad)
        r = width / 2
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def draw_icon(simple: bool) -> Image.Image:
    c = BASE / 2
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((0, 0, BASE - 1, BASE - 1), radius=BASE * 0.22, fill=TILE)
    if not simple:
        draw.rounded_rectangle((6, 6, BASE - 7, BASE - 7), radius=BASE * 0.21, outline=TILE_EDGE, width=10)

    ring_r = BASE * (0.355 if simple else 0.37)
    ring_w = BASE * (0.15 if simple else 0.095)
    track_r = ring_r + ring_w / 2
    draw.ellipse((c - track_r, c - track_r, c + track_r, c + track_r), outline=TRACK, width=round(ring_w))
    for color, start, end in ARCS:
        _arc_with_caps(draw, c, ring_r, ring_w, start, end, color)

    if not simple:
        core_r = BASE * 0.285
        draw.ellipse((c - core_r, c - core_r, c + core_r, c + core_r), fill=CORE, outline=CORE_EDGE, width=8)

    k = BASE / 72 * (1.18 if simple else 1.0)
    pts = [(c + (x - 36) * k, c + (y - 36) * k) for x, y in BOLT_POINTS]

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(pts, fill=BOLT_GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(BASE * (0.012 if simple else 0.02)))
    img = Image.alpha_composite(img, glow)

    bolt = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(bolt).polygon(pts, fill=BOLT, outline=BOLT_GLOW, width=6)
    img = Image.alpha_composite(img, bolt)

    # Keep the glow inside the rounded tile
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BASE - 1, BASE - 1), radius=BASE * 0.22, fill=255)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return img


def main() -> None:
    detailed, simple = draw_icon(False), draw_icon(True)
    frames = [
        (simple if size <= 32 else detailed).resize((size, size), Image.LANCZOS)
        for size in ICO_SIZES
    ]
    ico_path = OUT_DIR / "tokenwatch.ico"
    frames[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    detailed.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "tokenwatch.png")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()

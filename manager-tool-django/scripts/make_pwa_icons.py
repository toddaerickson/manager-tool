"""Generate the PWA icon set (roadmap PR 7) with the stdlib only.

Why no Pillow: the icons are generated once and committed; adding an
image dependency for a one-time build violates the no-net-complexity
guardrail. A PNG is just zlib-compressed scanlines in CRC'd chunks, so
this rasterizes the mark by hand: an "M" monogram built from four
quads, point-in-polygon tested per pixel with 4x supersampling.

Design (DESIGN.md tokens): slate-900 ground (#0f172a — the sidebar
brand chrome), accent teal mark (#14b8a6, accent-500 — reads better on
dark than accent-700). Full-bleed squares: Android masks via the
manifest's "maskable" purpose (the mark sits inside the 80% safe
zone), iOS rounds the apple-touch-icon itself. Flat two-color, no
gradients (forbidden-slop list).

Run from manager-tool-django/:  python scripts/make_pwa_icons.py
Outputs: static/icons/icon-512.png, icon-192.png, apple-touch-icon.png
"""

import struct
import zlib
from pathlib import Path

GROUND = (0x0F, 0x17, 0x2A)  # slate-900
MARK = (0x14, 0xB8, 0xA6)    # accent-500 teal

# The "M" in a 0..1 viewbox: two uprights + two diagonals meeting in a
# center valley. All strokes are quads; the union of the four shapes is
# the glyph. Kept inside x∈[0.18,0.82], y∈[0.26,0.74] — comfortably
# within the maskable 80% safe zone.
_W = 0.15  # upright stroke width
QUADS = [
    # left upright
    [(0.18, 0.26), (0.18 + _W, 0.26), (0.18 + _W, 0.74), (0.18, 0.74)],
    # right upright
    [(0.82 - _W, 0.26), (0.82, 0.26), (0.82, 0.74), (0.82 - _W, 0.74)],
    # left diagonal: top of left upright down to the center valley
    [(0.18, 0.26), (0.18 + _W, 0.26), (0.565, 0.66), (0.435, 0.66)],
    # right diagonal (mirror)
    [(0.82 - _W, 0.26), (0.82, 0.26), (0.565, 0.66), (0.435, 0.66)],
]


def _in_quad(quad, x, y):
    """Ray-cast point-in-polygon (convex or not, quads here)."""
    inside = False
    n = len(quad)
    for i in range(n):
        x1, y1 = quad[i]
        x2, y2 = quad[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                inside = not inside
    return inside


def _in_mark(x, y):
    return any(_in_quad(q, x, y) for q in QUADS)


def render(size, supersample=4):
    """Return raw RGB rows for a size x size icon."""
    rows = []
    ss = supersample
    step = 1.0 / (size * ss)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            hit = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px * ss + sx + 0.5) * step
                    y = (py * ss + sy + 0.5) * step
                    if _in_mark(x, y):
                        hit += 1
            a = hit / (ss * ss)  # coverage -> blend mark over ground
            row += bytes(
                round(g + (m - g) * a) for g, m in zip(GROUND, MARK)
            )
        rows.append(bytes(row))
    return rows


def write_png(path, size, rows):
    def chunk(tag, payload):
        data = tag + payload
        return (struct.pack(">I", len(payload)) + data
                + struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + r for r in rows)  # filter 0 per scanline
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
    print(f"wrote {path} ({size}x{size}, {len(png)} bytes)")


def main():
    out = Path(__file__).resolve().parent.parent / "static" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192),
                       ("apple-touch-icon.png", 180)):
        write_png(out / name, size, render(size))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate osintapp/data/app.ico without any third-party imaging library.

Pillow is not a dependency of this project and dragging one in just to draw an
icon would be a poor trade, so the icon is rendered here from signed-distance
fields and written out as a multi-size .ico by hand.

Run this only when the artwork changes; the generated file is committed.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 3

# Palette, matching the app's accent colours.
BG_TOP = (0x3B, 0x8D, 0xFF)
BG_BOTTOM = (0x1B, 0x5F, 0xD8)
GLASS = (0xFF, 0xFF, 0xFF)
LENS = (0x0D, 0x1B, 0x2E)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sdf_rounded_rect(x: float, y: float, half: float, radius: float) -> float:
    """Signed distance to a rounded square centred on (0.5, 0.5)."""
    dx = abs(x - 0.5) - (half - radius)
    dy = abs(y - 0.5) - (half - radius)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def _sdf_capsule(x: float, y: float, ax: float, ay: float,
                 bx: float, by: float, radius: float) -> float:
    """Signed distance to a thick line segment (the magnifier handle)."""
    pax, pay = x - ax, y - ay
    bax, bay = bx - ax, by - ay
    denominator = bax * bax + bay * bay
    h = _clamp((pax * bax + pay * bay) / denominator) if denominator else 0.0
    return math.hypot(pax - bax * h, pay - bay * h) - radius


def _coverage(distance: float, feather: float) -> float:
    """Convert a distance field to an alpha, giving anti-aliased edges."""
    return _clamp(0.5 - distance / feather)


def _blend(base, layer, alpha: float):
    return tuple(int(round(b + (l - b) * alpha)) for b, l in zip(base, layer))


def render(size: int):
    """Return BGRA rows (top-down) for one icon size."""
    scale = size * SUPERSAMPLE
    feather = 1.5 / scale

    # Geometry in unit space.
    ring_centre = (0.44, 0.44)
    ring_outer, ring_inner = 0.225, 0.150
    handle = (0.585, 0.585, 0.80, 0.80, 0.055)

    pixels = []
    for py in range(size):
        row = []
        for px in range(size):
            red = green = blue = alpha_total = 0.0

            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    x = (px + (sx + 0.5) / SUPERSAMPLE) / size
                    y = (py + (sy + 0.5) / SUPERSAMPLE) / size

                    plate = _coverage(_sdf_rounded_rect(x, y, 0.5, 0.115), feather)
                    if plate <= 0.0:
                        continue

                    # Vertical gradient background.
                    mix = y
                    colour = tuple(
                        int(round(top + (bottom - top) * mix))
                        for top, bottom in zip(BG_TOP, BG_BOTTOM)
                    )

                    distance_ring = math.hypot(x - ring_centre[0], y - ring_centre[1])
                    lens = _coverage(distance_ring - ring_inner, feather)
                    if lens > 0.0:
                        colour = _blend(colour, LENS, lens * 0.55)

                    ring = _coverage(distance_ring - ring_outer, feather) - \
                        _coverage(distance_ring - ring_inner, feather)
                    handle_alpha = _coverage(_sdf_capsule(x, y, *handle), feather)
                    glass = _clamp(max(ring, handle_alpha))
                    if glass > 0.0:
                        colour = _blend(colour, GLASS, glass)

                    red += colour[0] * plate
                    green += colour[1] * plate
                    blue += colour[2] * plate
                    alpha_total += plate

            samples = SUPERSAMPLE * SUPERSAMPLE
            alpha = alpha_total / samples
            if alpha <= 0.0:
                row.append((0, 0, 0, 0))
            else:
                row.append((
                    int(round(blue / alpha_total)),
                    int(round(green / alpha_total)),
                    int(round(red / alpha_total)),
                    int(round(alpha * 255)),
                ))
        pixels.append(row)
    return pixels


def encode_bmp(pixels) -> bytes:
    """A .ico image entry: BITMAPINFOHEADER + BGRA data + AND mask."""
    height = len(pixels)
    width = len(pixels[0])

    header = struct.pack(
        "<IiiHHIIiiII",
        40,             # biSize
        width,
        height * 2,     # doubled: colour data plus the AND mask
        1,              # biPlanes
        32,             # biBitCount
        0,              # biCompression (BI_RGB)
        width * height * 4,
        0, 0, 0, 0,
    )

    # Rows are stored bottom-up.
    colour = bytearray()
    for row in reversed(pixels):
        for b, g, r, a in row:
            colour += bytes((b, g, r, a))

    # 1bpp AND mask, rows padded to a 4-byte boundary. Fully transparent for
    # 32bpp icons, but Windows still expects it to be present.
    mask_row_bytes = ((width + 31) // 32) * 4
    mask = bytes(mask_row_bytes * height)

    return header + bytes(colour) + mask


def main() -> None:
    images = [encode_bmp(render(size)) for size in SIZES]

    directory = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries = b""
    for size, data in zip(SIZES, images):
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,   # 0 means 256 in the ICO format
            0 if size >= 256 else size,
            0, 0, 1, 32,
            len(data), offset,
        )
        offset += len(data)

    target = Path(__file__).resolve().parent.parent / "osintapp" / "data" / "app.ico"
    target.write_bytes(directory + entries + b"".join(images))
    print(f"wrote {target} ({target.stat().st_size:,} bytes, sizes: {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()

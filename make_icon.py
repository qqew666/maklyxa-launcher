#!/usr/bin/env python3
"""Генератор иконки MaklyxaLauncher (icon.png + icon.ico), без внешних библиотек."""
import struct
import zlib
from pathlib import Path

SIZES = [16, 32, 48, 64, 128, 256]
TILE_TOP = (0, 214, 158, 255)    # верх градиента плашки
TILE_BOT = (0, 140, 104, 255)    # низ градиента плашки
DARK = (11, 17, 24, 255)         # монограмма «M»
SS = 3                           # суперсэмплинг (антиалиасинг)

# «M» — ломаная: левая стойка, диагонали вниз-вверх, правая стойка
M_POINTS = [(0.305, 0.710), (0.305, 0.290), (0.500, 0.540),
            (0.695, 0.290), (0.695, 0.710)]
M_HALF_STROKE = 0.082
TILE_RADIUS = 0.225


def sd_roundrect(cx, cy, half, r):
    qx = abs(cx) - (half - r)
    qy = abs(cy) - (half - r)
    mx = qx if qx > 0 else 0.0
    my = qy if qy > 0 else 0.0
    outside = (mx * mx + my * my) ** 0.5
    m = qx if qx > qy else qy
    inside = m if m < 0 else 0.0
    return outside + inside - r


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / l2))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return (dx * dx + dy * dy) ** 0.5


def lerp_color(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))


def render(size):
    half = size / 2.0
    r = size * TILE_RADIUS
    hw = size * M_HALF_STROKE
    pts = [(x * size, y * size) for x, y in M_POINTS]
    px = bytearray(size * size * 4)
    step = 1.0 / SS
    total = SS * SS
    for y in range(size):
        for x in range(size):
            ra = ga = ba = covered = 0
            for j in range(SS):
                sy = y + (j + 0.5) * step
                for i in range(SS):
                    sx = x + (i + 0.5) * step
                    cx, cy = sx - half, sy - half
                    if sd_roundrect(cx, cy, half, r) > 0:
                        continue
                    col = lerp_color(TILE_TOP, TILE_BOT, sy / size)
                    d = min(seg_dist(sx, sy, pts[k][0], pts[k][1],
                                     pts[k + 1][0], pts[k + 1][1])
                            for k in range(len(pts) - 1))
                    if d <= hw:
                        col = DARK
                    ra += col[0]
                    ga += col[1]
                    ba += col[2]
                    covered += 1
            o = (y * size + x) * 4
            if covered:
                px[o] = ra // covered
                px[o + 1] = ga // covered
                px[o + 2] = ba // covered
                px[o + 3] = 255 * covered // total
    return bytes(px)


def png_rgba(w, h, rgba):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_from_pngs(items):
    out = struct.pack("<HHH", 0, 1, len(items))
    offset = 6 + 16 * len(items)
    entries = b""
    for size, data in items:
        b = size if size < 256 else 0
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + entries + b"".join(d for _, d in items)


def main():
    out = Path(__file__).resolve().parent
    items = []
    for size in SIZES:
        items.append((size, png_rgba(size, size, render(size))))
        print(f"  rendered {size}x{size}")
    png256 = dict(items)[256]
    (out / "icon.png").write_bytes(png256)
    (out / "icon.ico").write_bytes(ico_from_pngs(items))
    print(f"OK: {out / 'icon.png'}")
    print(f"OK: {out / 'icon.ico'}")


if __name__ == "__main__":
    main()

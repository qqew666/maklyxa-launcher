#!/usr/bin/env python3
"""Генератор иконки MaklyxaLauncher (icon.png + icon.ico), без внешних библиотек."""
import struct
import zlib
from pathlib import Path

SIZES = [16, 32, 48, 64, 128, 256]
BG = (21, 21, 43, 255)      # тёмно-синий фон
GRN = (0, 196, 140, 255)    # зелёный кружок
WHT = (245, 245, 250, 255)  # белый треугольник play
SS = 3                      # суперсэмплинг (антиалиасинг)


def sd_roundrect(cx, cy, half, r):
    qx = abs(cx) - (half - r)
    qy = abs(cy) - (half - r)
    mx = qx if qx > 0 else 0.0
    my = qy if qy > 0 else 0.0
    outside = (mx * mx + my * my) ** 0.5
    m = qx if qx > qy else qy
    inside = m if m < 0 else 0.0
    return outside + inside - r


def in_triangle(x, y, verts):
    (x1, y1), (x2, y2), (x3, y3) = verts
    d1 = (x - x2) * (y1 - y2) - (x1 - x2) * (y - y2)
    d2 = (x - x3) * (y2 - y3) - (x2 - x3) * (y - y3)
    d3 = (x - x1) * (y3 - y1) - (x3 - x1) * (y - y1)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def render(size):
    half = size / 2.0
    r = size * 0.24
    circ_r = size * 0.335
    tri = [(-0.115 * size + half, -0.185 * size + half),
           (0.205 * size + half, half),
           (-0.115 * size + half, 0.185 * size + half)]
    px = bytearray(size * size * 4)
    step = 1.0 / SS
    n = SS * SS
    for y in range(size):
        for x in range(size):
            ra = ga = ba = aa = 0
            for j in range(SS):
                sy = y + (j + 0.5) * step
                for i in range(SS):
                    sx = x + (i + 0.5) * step
                    cx = sx - half
                    cy = sy - half
                    if sd_roundrect(cx, cy, half, r) > 0:
                        continue
                    cr, cg, cb, ca = BG
                    if cx * cx + cy * cy <= circ_r * circ_r:
                        cr, cg, cb, ca = GRN
                    if in_triangle(sx, sy, tri):
                        cr, cg, cb, ca = WHT
                    ra += cr
                    ga += cg
                    ba += cb
                    aa += ca
            o = (y * size + x) * 4
            px[o] = ra // n
            px[o + 1] = ga // n
            px[o + 2] = ba // n
            px[o + 3] = aa // n
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

# -*- coding: utf-8 -*-
"""
生成应用图标 app.ico（纯标准库，零依赖）：
蓝紫渐变背景 + 白色 ⇄ 双向箭头，256x256 PNG-in-ICO（Vista+ 兼容）。
用法：python scripts/make_icon.py
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app.ico"
W = H = 256


def lerp(a, b, t):
    return int(a + (b - a) * t)


def triangle(pixels, pts, color):
    (x1, y1), (x2, y2), (x3, y3) = pts
    minx = max(0, min(x1, x2, x3))
    maxx = min(W - 1, max(x1, x2, x3))
    miny = max(0, min(y1, y2, y3))
    maxy = min(H - 1, max(y1, y2, y3))

    def sign(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    for y in range(miny, maxy + 1):
        for x in range(minx, maxx + 1):
            d1 = sign(x1, y1, x2, y2, x, y)
            d2 = sign(x2, y2, x3, y3, x, y)
            d3 = sign(x3, y3, x1, y1, x, y)
            has_neg = d1 < 0 or d2 < 0 or d3 < 0
            has_pos = d1 > 0 or d2 > 0 or d3 > 0
            if not (has_neg and has_pos):
                pixels[y][x] = color


def fill_rect(pixels, x0, y0, x1, y1, color):
    for y in range(max(0, y0), min(H, y1)):
        for x in range(max(0, x0), min(W, x1)):
            pixels[y][x] = color


def build_png():
    px = [[(0, 0, 0, 0)] * W for _ in range(H)]
    # 对角渐变：左上蓝 #4CC2FF -> 右下紫 #8B7CF6
    for y in range(H):
        for x in range(W):
            t = (x + y) / (2 * W)
            px[y][x] = (lerp(76, 139, t), lerp(194, 124, t), lerp(255, 246, t), 255)
    WHITE = (255, 255, 255, 255)
    # 左向箭头（尖朝左）
    triangle(px, [(76, 128), (122, 94), (122, 162)], WHITE)
    # 右向箭头（尖朝右）
    triangle(px, [(180, 128), (134, 94), (134, 162)], WHITE)
    # 中间两短横（⇄ 符号特征）
    fill_rect(px, 122, 108, 134, 119, WHITE)
    fill_rect(px, 122, 137, 134, 148, WHITE)

    raw = b""
    for y in range(H):
        raw += b"\x00" + b"".join(struct.pack("4B", *px[y][x]) for x in range(W))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def build_ico(png: bytes) -> bytes:
    # ICONDIR(6) + ICONDIRENTRY(16) + PNG
    head = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return head + entry + png


if __name__ == "__main__":
    png = build_png()
    OUT.write_bytes(build_ico(png))
    print("ICO 生成完成 ->", OUT, f"({OUT.stat().st_size} bytes)")

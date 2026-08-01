"""Note / GitHubソーシャル用サムネ生成（1280x670）。

左にタイトル3行（最終行を強調色）＋サブ＋バッジ、右に作品画像（透過PNG）を配置。
日本語は Noto Sans CJK JP を使用（無ければ --font で指定）。

例:
  python3 make_thumbnail.py \
    --title "歯科スキャンから,中空オープン模型を,自動生成" \
    --sub "AI × 3Dプリント ｜ オープンソース公開" \
    --badge "dental-3d" \
    --image thumb_model.png \
    --out docs/thumbnail.png

依存: Pillow
"""
from __future__ import annotations

import argparse
from PIL import Image, ImageDraw, ImageFont

NOTO = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
W, H = 1280, 670
TEAL = (16, 124, 134)
DEEP = (8, 30, 40)
GOLD = (233, 200, 110)
WHITE = (255, 255, 255)
SUBCOL = (220, 238, 240)


def _grad(c1: tuple, c2: tuple) -> Image.Image:
    bg = Image.new("RGB", (W, H))
    px = bg.load()
    for y in range(H):
        for x in range(0, W, 2):
            t = x / W * 0.6 + y / H * 0.4
            col = (int(c1[0] + (c2[0] - c1[0]) * t),
                   int(c1[1] + (c2[1] - c1[1]) * t),
                   int(c1[2] + (c2[2] - c1[2]) * t))
            px[x, y] = col
            if x + 1 < W:
                px[x + 1, y] = col
    return bg


def make(title_lines: list[str], sub: str, badge: str, image: str | None,
         out: str, font_path: str = NOTO) -> str:
    bg = _grad(TEAL, DEEP)
    draw = ImageDraw.Draw(bg, "RGBA")
    for gy in range(40, H, 46):                      # ドットグリッド
        for gx in range(40, W, 46):
            draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=(255, 255, 255, 16))
    if image:                                        # 右に作品画像（透過）
        m = Image.open(image).convert("RGBA")
        bbox = m.getbbox()
        if bbox:
            m = m.crop(bbox)
        th = int(H * 0.97)
        tw = int(m.width * (th / m.height))
        m = m.resize((tw, th), Image.LANCZOS)
        bg.paste(m, (W - tw + int(tw * 0.04), (H - th) // 2), m)
    f_title = ImageFont.truetype(font_path, 84, index=0)
    f_sub = ImageFont.truetype(font_path, 33, index=0)
    f_badge = ImageFont.truetype(font_path, 30, index=0)
    x = 70
    for i, line in enumerate(title_lines[:3]):
        col = GOLD if i == len(title_lines[:3]) - 1 else WHITE
        draw.text((x, 108 + i * 98), line, font=f_title, fill=col)
    draw.rectangle([x, 402, x + 330, 408], fill=GOLD)
    if sub:
        draw.text((x, 430), sub, font=f_sub, fill=SUBCOL)
    if badge:
        bw = 26 * 2 + int(f_badge.getlength(badge))
        draw.rounded_rectangle([x, 496, x + bw, 548], radius=26, fill=GOLD)
        draw.text((x + 26, 505), badge, font=f_badge, fill=(8, 40, 46))
    bg.save(out, "PNG")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="3行までをカンマ区切り（最終行が強調色）")
    ap.add_argument("--sub", default="")
    ap.add_argument("--badge", default="")
    ap.add_argument("--image", default=None, help="右に置く透過PNG（任意）")
    ap.add_argument("--out", default="thumbnail.png")
    ap.add_argument("--font", default=NOTO)
    a = ap.parse_args()
    path = make([s.strip() for s in a.title.split(",")], a.sub, a.badge,
                a.image, a.out, a.font)
    print("saved:", path)


if __name__ == "__main__":
    main()

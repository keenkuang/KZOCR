"""临时脚本：奇数页 cv2 块可视化（用生产代码 _find_body_boundaries 的 is_odd 分支）。

奇数页规则：left=min(宽行lx)-20 ; right=w ; top/bottom 用页眉/页脚+投影检测。
逐行垂直投影求真实左右边界，按 w*0.5 分宽/窄行：
  - 绿框 = 宽行(正文，参与 left 边界)
  - 橙框 = 窄行(左侧侧眉/竖排/页码，被宽行过滤排除)
  - 青框 = 当前 cv2 版心；红竖线 = 左侧裁切线
并在块旁标注 bw 与 *（宽行）。

输出：crop_preview/odd/page_XXXX_blocks.png
用法：python _preview_odd.py [页号...]  （默认 31 33 35 37 39）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _find_body_boundaries,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/odd")
OUT.mkdir(parents=True, exist_ok=True)

WIDE_RATIO = 0.5


def per_block(img, lines, w):
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    out = []
    for (x1, y1, x2, y2) in lines:
        row = gray[y1:y2, :]
        col_proj = np.mean(row < 128, axis=0)
        lx = 0
        for cx in range(w):
            if col_proj[cx] > 0.01:
                lx = cx
                break
        rx = w
        for cx in range(w - 1, -1, -1):
            if col_proj[cx] > 0.01:
                rx = cx
                break
        bw = rx - lx if lx < rx else 0
        out.append((lx, y1, rx, y2, bw))
    return out


def doclayout_box(img):
    model = _get_doclayout_model()
    if model is None:
        return None
    try:
        results = list(model.predict(img, batch_size=1))
    except Exception:
        return None
    if not results:
        return None
    try:
        boxes = _extract_doclayout_boxes(results[0])
    except Exception:
        return None
    body = [b for b in boxes if b.get("label") in _BODY_LABELS]
    if not body:
        return None
    h, w = img.shape[:2]
    xs = [b["coordinate"][0] for b in body]
    ys = [b["coordinate"][1] for b in body]
    xe = [b["coordinate"][2] for b in body]
    ye = [b["coordinate"][3] for b in body]
    return (
        max(0, int(min(xs)) - 15),
        max(0, int(min(ys)) - 15),
        min(w, int(max(xe)) + 15),
        min(h, int(max(ye)) + 10),
    )


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or [p for p in range(31, 41) if p % 2 == 1]
    rows = []
    for n in pages:
        if n % 2 == 0:
            continue
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            print(f"[skip] 缺失: {p}")
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]

        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        blocks = per_block(img, merged, w)
        wide = [b for b in blocks if b[4] > w * WIDE_RATIO]
        top, bottom, left, right = _find_body_boundaries(img, merged, 10, page_num=n)
        dl = doclayout_box(img)

        print("=" * 78)
        print(f"PAGE {n:04d}  size={w}x{h}  块数={len(blocks)} 宽行={len(wide)} 窄行={len(blocks)-len(wide)}")
        print(f"  宽行 lx_min-20 = {min((b[0] for b in wide), default=0)-20}  → left={left}")
        print(f"  cv2 (t,b,l,r)=({top},{bottom},{left},{right})  裁后={right-left}x{bottom-top}")
        if dl:
            print(f"  doclay (l,t,r,b)=({dl[0]},{dl[1]},{dl[2]},{dl[3]})  裁后={dl[2]-dl[0]}x{dl[3]-dl[1]}")
            print(f"  偏差   l={left-dl[0]:+d} r={right-dl[2]:+d} t={top-dl[1]:+d} b={bottom-dl[3]:+d}")
        rows.append((n, w, h, left, right, right - left, top, bottom))

        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        for b in blocks:
            color = (0, 200, 0) if b[4] > w * WIDE_RATIO else (230, 140, 0)
            d.rectangle([b[0], b[1], b[2], b[3]], outline=color, width=2)
            if font:
                d.text((b[0], max(0, b[1] - 12)), f"bw={b[4]}{'*' if b[4] > w*WIDE_RATIO else ''}",
                       fill=color, font=font)
        d.rectangle([left, top, right, bottom], outline=(0, 200, 220), width=4)
        d.line([left, 0, left, h], fill=(220, 0, 0), width=3)
        vis.save(OUT / f"page_{n:04d}_blocks.png")

    print("\n" + "-" * 78)
    print(f"{'页':>4} {'原宽':>5} {'左':>5} {'右':>5} {'裁后宽':>6} {'上':>5} {'下':>5}")
    for n, w, h, l, r, cw, t, b in rows:
        print(f"{n:>4} {w:>5} {l:>5} {r:>5} {cw:>6} {t:>5} {b:>5}")
    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()

"""临时脚本：把奇数页规则(left_page_rule)结构镜像到偶数页(right_page_rule)，出图看效果。

镜像映射（left↔right）：
  odd  left = max(120, narrow_min_lx-20, header_x_min-20, min(wide lx)-20) ; right = w
  even right = min(w, narrow_max_rx+20, footer_max_rx+20, max(wide rx)+20, w-120) ; left = 0
页眉/页脚检测逻辑不变（顶部 15% + 高度>30；底部 15%；间距<=20 合并相邻行）。

输出：crop_preview/even_cv2/mirror/
  page_XXXX_blocks.png：绿=宽行 橙=窄行 青=镜像规则版心 红=右裁切线
  page_XXXX_cropped.png：镜像规则裁后图（含 _post_trim_borders）
并打印 (t,b,l,r)、doclayout 对照与偏差。

用法：python _preview_even_mirror.py [页号...]  （默认 32 34 36 38 40）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _post_trim_borders,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/even_cv2/mirror")
OUT.mkdir(parents=True, exist_ok=True)

WIDE_RATIO = 0.5
FLOOR = 120          # 镜像 odd 左边界下限 120：右边界至多裁到 w-120


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


def right_page_rule(blocks, h, w):
    """left_page_rule 的结构镜像：侧眉在右的偶数页。返回 (left, top, right, bottom)。"""
    wide = [b for b in blocks if b[4] > w * WIDE_RATIO]
    narrow = [b for b in blocks if b[4] <= w * WIDE_RATIO]

    # 页眉（顶部）
    headers = []
    if blocks and blocks[0][1] < h * 0.15 and (blocks[0][3] - blocks[0][1]) > 30:
        if len(blocks) >= 2:
            gap = blocks[1][1] - blocks[0][3]
            headers = [blocks[0], blocks[1]] if gap <= 20 else [blocks[0]]
        else:
            headers = [blocks[0]]
    # 页脚（底部）
    footers = []
    if blocks and blocks[-1][3] > h * 0.85:
        if len(blocks) >= 2:
            gap = blocks[-1][1] - blocks[-2][3]
            footers = [blocks[-2], blocks[-1]] if gap <= 20 else [blocks[-1]]
        else:
            footers = [blocks[-1]]

    left = 0  # 镜像 odd 的 right=w

    # 右边界：镜像 odd 左边界约束
    narrow_max_rx = max((b[2] for b in narrow), default=w)
    footer_max_rx = max((b[2] for b in footers), default=0)
    right_cands = [w]                                       # 默认全宽（镜像 odd right=w）
    if wide:
        right_cands.append(max(b[2] for b in wide) + 20)   # 镜像 min(wide lx)-20
    if narrow:
        right_cands.append(narrow_max_rx + 20)             # 镜像 narrow_min_lx-20
    if footers:
        right_cands.append(footer_max_rx + 20)             # 镜像 header_x_min-20
    right = min(right_cands)
    right = min(w - FLOOR, right)                           # 镜像左边界 120 下限

    # 顶/底（与 left_page_rule 一致）
    top = (
        max(0, max(b[3] for b in headers) + 20)
        if headers
        else (max(0, blocks[0][1] - 15) if blocks else 0)
    )
    bottom = (
        min(h, min(b[1] for b in footers) - 15)
        if footers
        else (min(h, blocks[-1][3] + 15) if blocks else h)
    )
    return left, top, right, bottom


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
    pages = [int(x) for x in sys.argv[1:]] or [32, 34, 36, 38, 40]
    rows = []
    for n in pages:
        if n % 2 == 1:
            print(f"[skip] 页 {n} 为奇数页")
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
        left, top, right, bottom = right_page_rule(blocks, h, w)
        dl = doclayout_box(img)

        print("=" * 78)
        print(f"PAGE {n:04d}  size={w}x{h}  宽行={len(wide)}")
        print(f"  镜像规则 (l,t,r,b)=({left},{top},{right},{bottom})  裁后={right-left}x{bottom-top}")
        if dl:
            print(f"  doclay   (l,t,r,b)={dl}  裁后={dl[2]-dl[0]}x{dl[3]-dl[1]}")
            print(f"  偏差     l={left-dl[0]:+d} r={right-dl[2]:+d} t={top-dl[1]:+d} b={bottom-dl[3]:+d}")
        rows.append((n, w, h, left, right, right - left, top, bottom))

        # block 图
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
        d.line([right, 0, right, h], fill=(220, 0, 0), width=3)
        vis.save(OUT / f"page_{n:04d}_blocks.png")

        cropped = img[top:bottom, left:right].copy()
        cropped = _post_trim_borders(cropped)
        Image.fromarray(cropped).save(OUT / f"page_{n:04d}_cropped.png")

    print("\n" + "-" * 78)
    print(f"{'页':>4} {'原宽':>5} {'左':>5} {'右':>5} {'裁后宽':>6} {'上':>5} {'下':>5}")
    for n, w, h, l, r, cw, t, b in rows:
        print(f"{n:>4} {w:>5} {l:>5} {r:>5} {cw:>6} {t:>5} {b:>5}")
    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()

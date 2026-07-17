"""临时脚本：用用户给出的 right 公式跑偶数页 cv2 版心，出图看效果。

用户公式（right 边界）：
  M  = 所有行 x2 最大值（右侧整列最右缘）
  X  = 排除 M 右侧 60px 尾部后、剩余行的最大 x2（正文最右缘）
  right = M - (M - X)/2        # M 与 X 的中点
（同时打印 M、X 供核对；另列字面版 right = M - X/2 以对比）

left=0；top/bottom 沿用 header/footer 检测（与镜像规则一致）。
输出：crop_preview/even_cv2/formula/   blocks 图 + cropped 图 + doclayout 对照
用法：python _preview_even_formula.py [页号...]  （默认 32 34 36 38 40）
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
OUT = Path("crop_preview/even_cv2/formula")
OUT.mkdir(parents=True, exist_ok=True)

GAP = 40
PAD = 28


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


def user_right(blocks, w):
    M = max(b[2] for b in blocks) if blocks else w
    body = [b for b in blocks if b[2] <= M - GAP]   # 排除右侧 40px 尾部
    X = max(b[2] for b in body) if body else M
    right = int(M - (M - X) / 2 - PAD)              # M 与 X 中点再左移 PAD
    literal = int(M - X / 2 - PAD)                   # 字面版：M - X/2 - 18
    return M, X, right, literal


def user_left(blocks):
    """left = max(60, 大于100的x1的最小值 - 20)（用左边缘 x1）。"""
    cands = [b[0] for b in blocks if b[0] > 100]
    left = min(cands) - 20 if cands else 0
    return max(60, left)


def user_top_bottom(blocks, h):
    """镜像奇数页规则的页眉/页脚检测，返回 (top, bottom)。

    页眉：首行位于顶部 15% 且高度>30；与下一行间距<=20 则合并首两行。
    页脚：末行位于页底 15%；与上一行间距<=20 则合并末两行。
    top = 页眉块底边 + 20（排除页眉）；bottom = 页脚块顶边 - 15（排除页脚）。
    """
    headers = []
    if blocks and blocks[0][1] < h * 0.15 and (blocks[0][3] - blocks[0][1]) > 30:
        if len(blocks) >= 2:
            gap = blocks[1][1] - blocks[0][3]
            headers = [blocks[0], blocks[1]] if gap <= 20 else [blocks[0]]
        else:
            headers = [blocks[0]]
    footers = []
    if blocks and blocks[-1][3] > h * 0.85:
        if len(blocks) >= 2:
            gap = blocks[-1][1] - blocks[-2][3]
            footers = [blocks[-2], blocks[-1]] if gap <= 20 else [blocks[-1]]
        else:
            footers = [blocks[-1]]

    if headers:
        top = max(0, max(b[3] for b in headers) + 20)
    else:
        top = max(0, blocks[0][1] - 15) if blocks else 0
    if footers:
        bottom = min(h, min(b[1] for b in footers) - 15)
    else:
        bottom = min(h, blocks[-1][3] + 15) if blocks else h
    return top, bottom


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
        M, X, right, literal = user_right(blocks, w)
        right = min(w, max(0, right))
        left = user_left(blocks)
        top, bottom = user_top_bottom(blocks, h)
        dl = doclayout_box(img)

        print("=" * 78)
        print(f"PAGE {n:04d}  M={M}  X={X}  right={right}  left={left}  (t,b)=({top},{bottom})")
        if dl:
            print(f"  doclay (l,t,r,b)=({dl[0]},{dl[1]},{dl[2]},{dl[3]})  "
                  f"偏差 左={left-dl[0]:+d} 右={right-dl[2]:+d} 上={top-dl[1]:+d} 下={bottom-dl[3]:+d}")
        rows.append((n, w, M, X, right, left, top, bottom,
                     dl[0] if dl else None, dl[1] if dl else None,
                     dl[2] if dl else None, dl[3] if dl else None))

        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        for b in blocks:
            color = (0, 200, 0) if b[4] > w * 0.5 else (230, 140, 0)
            d.rectangle([b[0], b[1], b[2], b[3]], outline=color, width=2)
        d.rectangle([left, top, right, bottom], outline=(0, 200, 220), width=4)
        d.line([right, 0, right, h], fill=(220, 0, 0), width=3)
        d.line([left, 0, left, h], fill=(220, 0, 0), width=3)
        d.line([0, top, w, top], fill=(0, 0, 220), width=2)
        d.line([0, bottom, w, bottom], fill=(0, 0, 220), width=2)
        vis.save(OUT / f"page_{n:04d}_blocks.png")
        cropped = img[top:bottom, left:right].copy()
        cropped = _post_trim_borders(cropped)
        Image.fromarray(cropped).save(OUT / f"page_{n:04d}_cropped.png")

    print("\n" + "-" * 78)
    print(f"{'页':>4} {'M':>5} {'X':>5} {'right':>6} {'left':>5} {'top':>5} {'bottom':>6} {'dl_l':>5} {'dl_r':>5}")
    for n, w, M, X, right, left, top, bottom, dl_l, dl_t, dl_r, dl_b in rows:
        print(f"{n:>4} {M:>5} {X:>5} {right:>6} {left:>5} {top:>5} {bottom:>6} "
              f"{str(dl_l):>5} {str(dl_r):>5}")
    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()

"""临时验证：用户提出的「左面页面」版心裁切规则（侧眉在左）。

规则（用户定义）：
  首行：在顶部 15% 且高度>30px 且 lx>100 → 页眉；left=页眉lx_min-15, top=页眉y_min-10
  末行：在页底 15% 且与上一行距离>20px → 页脚（距离≤20 则末行+上一行均为页脚）；
        bottom=页脚y_max+10
  右侧：right=所有宽行(rx)最大值+20

仅适用于「侧眉在左」的页面。本脚本在 0031 上验证，并对比 doclayout。

用法：python _rule_leftpage.py 31
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _find_body_boundaries,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


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
    results = list(model.predict(img, batch_size=1))
    if not results:
        return None
    boxes = _extract_doclayout_boxes(results[0])
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


def left_page_rule(blocks, h, w):
    """定稿规则（排除页眉页脚；与 _verify_leftpage_rule 一致）：
    left=max(页眉x_min-20, 窄行x_min-20, 120); right=宽行x_max+20;
    top=页眉块底边+20(排除页眉); bottom=页脚块顶边-15(排除页脚)。
    奇偶通用：右侧页侧眉为窄行，自然被宽行排除在 right 外。"""
    wide_th = w * 0.5
    wide = [b for b in blocks if b[4] > wide_th]
    narrow = [b for b in blocks if b[4] <= wide_th]
    # 页眉：首行 在顶部15% 且 高度>30；与下一行距离>20→仅首行，<=20→首行+第二行
    headers = []
    if blocks and blocks[0][1] < h * 0.15 and (blocks[0][3] - blocks[0][1]) > 30:
        if len(blocks) >= 2:
            gap = blocks[1][1] - blocks[0][3]
            headers = [blocks[0], blocks[1]] if gap <= 20 else [blocks[0]]
        else:
            headers = [blocks[0]]
    # 页脚：末行 在页底15% 且与上一行距离>20；<=20→末行+上一行
    footers = []
    if blocks and blocks[-1][3] > h * 0.85:
        if len(blocks) >= 2:
            gap = blocks[-1][1] - blocks[-2][3]
            footers = [blocks[-2], blocks[-1]] if gap <= 20 else [blocks[-1]]
        else:
            footers = [blocks[-1]]
    # 右侧：宽行 x 大值 + 20
    right = min(w, max(b[2] for b in wide) + 20) if wide else w
    # 左侧：max(页眉x_min-20, 窄行x_min-20, 120)
    narrow_min_lx = min((b[0] for b in narrow), default=0)
    left_cands = [120, narrow_min_lx - 20]
    if headers:
        left_cands.append(min(b[0] for b in headers) - 20)
    left = max(0, max(left_cands))
    # 顶部（排除页眉）：页眉块底边 + 20
    top = (
        max(0, max(b[3] for b in headers) + 20)
        if headers
        else (max(0, blocks[0][1] - 15) if blocks else 0)
    )
    # 底部（排除页脚）：页脚块顶边 - 15
    bottom = (
        min(h, min(b[1] for b in footers) - 15)
        if footers
        else (min(h, blocks[-1][3] + 15) if blocks else h)
    )
    return dict(
        headers=headers, footers=footers,
        left=left, top=top, narrow_min_lx=narrow_min_lx,
        right=right, bottom=bottom,
    )


def main() -> None:
    for n in ([int(x) for x in sys.argv[1:]] or [31]):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            print(f"[skip] {p}")
            continue
        img = load_rgb(p)
        h, w = img.shape[:2]
        print("=" * 84)
        print(f"PAGE {n:04d}  (左面页面·侧眉在左)  {w}x{h}")

        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        blocks = per_block(img, merged, w)
        old = _find_body_boundaries(img, merged, 10, n)
        res = left_page_rule(blocks, h, w)
        dl = doclayout_box(img)

        print(f"\n[旧 cv2] (top,bottom,left,right) = {old}")
        print(f"[新规则] 页眉块 = {[ (b[0],b[1],b[2],b[3]) for b in res['headers'] ]}")
        print(f"         页脚块 = {[ (b[0],b[1],b[2],b[3]) for b in res['footers'] ]}")
        print(
            f"         窄行x_min={res['narrow_min_lx']}  "
            f"left=max(min(页眉x_min-15,窄行x_min-15),115)={res['left']}  "
            f"top(页眉底+20)={res['top']}  "
            f"right(宽行x_max+20)={res['right']}  bottom(页脚y_max+10)={res['bottom']}"
        )
        lit = (res['top'], res['bottom'], res['left'], res['right'])
        print(f"         → 按你字面规则版心: {lit}  裁后 {lit[1]-lit[0]}x{lit[3]-lit[2]}")
        if dl:
            print(f"[doclayout] (top,bottom,left,right) = {dl}  裁后 {dl[3]-dl[1]}x{dl[2]-dl[0]}")

        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        for blk in res['headers']:
            d.rectangle([blk[0], blk[1], blk[2], blk[3]], outline=(0, 160, 0), width=3)
        for blk in res['footers']:
            d.rectangle([blk[0], blk[1], blk[2], blk[3]], outline=(0, 0, 200), width=3)
        d.rectangle([res['left'], res['top'], res['right'], res['bottom']],
                    outline=(255, 140, 0), width=4)
        if dl:
            d.rectangle([dl[0], dl[1], dl[2], dl[3]], outline=(0, 200, 220), width=4)
        outp = OUT / f"page_{n:04d}_rule.png"
        vis.save(outp)
        print(f"\n[图] {outp}  (绿=页眉 / 蓝=页脚 / 橙=新规则版心(按字面) / 青=doclayout)")


if __name__ == "__main__":
    main()

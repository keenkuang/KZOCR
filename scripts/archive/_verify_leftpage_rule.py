"""临时验证：按新规则(侧眉在左的页面)实现 cv2 版心，对照 doclayout 真值出图。

不涉及生产代码 layout_crop.py；仅验证规则效果。
用法:
    python _verify_leftpage_rule.py 31 33 35 37 39

输出:
    crop_preview/page_XXXX_newrule.png：原图叠加 红=新规则 / 青=doclayout 版心框
    终端打印新版心坐标与 doclayout 真值及偏差。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from kzocr.engine.layout_crop import (
    _detect_text_lines,
    _merge_nearby,
    _get_doclayout_model,
    _extract_doclayout_boxes,
    _BODY_LABELS,
)

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview")
OUT.mkdir(parents=True, exist_ok=True)

# --- 规则参数（便于调）---
HEADER_RATIO = 0.15      # 页眉/页脚判定：位于顶部/底部 15% 内
HEADER_MIN_H = 30        # 页眉首行最小高度
HF_GAP = 20              # 页眉(首行与下一行) / 页脚(末行与上一行) 间距阈值
HEADER_Y_PAD = 20        # 页眉 y 内缩（用户指定 +20）
FOOTER_Y_PAD = 15        # 页脚 y 外扩（用户指定 -15）
LEFT_MIN = 120           # 左边界下限
RIGHT_PAD = 20           # 右侧宽行外扩
FALLBACK_PAD = 15        # 无页眉/页脚时 top/bottom 兜底 padding


def _row_info(img: np.ndarray, lines: list[tuple[int, int, int, int]], w: int, h: int) -> list[dict]:
    """复刻 _find_body_boundaries 的垂直投影：取每行暗像素真实左右边界（非整页宽）。"""
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    out: list[dict] = []
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
        out.append({"x1": lx, "y1": y1, "x2": rx, "y2": y2, "bw": rx - lx, "wide": (rx - lx) > w * 0.5})
    return out


def new_rule(img: np.ndarray, lines: list[tuple[int, int, int, int]],
             w: int, h: int, page_num: int) -> tuple[int, int, int, int]:
    """新规则（侧眉在左的页面）。返回 (top, bottom, left, right)。"""
    info = _row_info(img, lines, w, h)
    n = len(info)
    is_left = (page_num % 2 == 1)  # 侧眉/页眉在左

    # --- 页眉（顶部）---
    header = None
    if n >= 1 and info[0]["y1"] < h * HEADER_RATIO and (info[0]["y2"] - info[0]["y1"]) > HEADER_MIN_H:
        if n >= 2 and (info[1]["y1"] - info[0]["y2"]) > HF_GAP:
            header = [info[0]]
        else:
            header = [info[0], info[1]] if n >= 2 else [info[0]]
    if header:
        # 裁掉页眉：版心从页眉块底边 + HEADER_Y_PAD 开始（排除页眉）
        top = max(0, max(b["y2"] for b in header) + HEADER_Y_PAD)
        hx_min = min(b["x1"] for b in header)
    else:
        top = max(0, info[0]["y1"] - FALLBACK_PAD)
        hx_min = None

    # --- 页脚（底部）---
    footer = None
    if n >= 1 and info[-1]["y2"] > h * (1 - HEADER_RATIO):
        if n >= 2 and (info[-1]["y1"] - info[-2]["y2"]) > HF_GAP:
            footer = [info[-1]]
        else:
            footer = [info[-2], info[-1]] if n >= 2 else [info[-1]]
    if footer:
        # 裁掉页脚：版心到页脚块顶边 - FOOTER_Y_PAD 结束（排除页脚）
        bottom = min(h, min(b["y1"] for b in footer) - FOOTER_Y_PAD)
    else:
        bottom = min(h, info[-1]["y2"] + FALLBACK_PAD)

    # --- 左 (侧眉在左的页面适用本规则) ---
    if is_left:
        narrow_xmin = min((b["x1"] for b in info if not b["wide"]), default=None)
        left_cands = [LEFT_MIN]
        if hx_min is not None:
            left_cands.append(hx_min - 20)
        if narrow_xmin is not None:
            left_cands.append(narrow_xmin - 20)
        left = max(0, max(left_cands))
    else:
        left = 0  # 右侧页暂不动（左侧无侧眉）

    # --- 右 ---
    wide_rx = [b["x2"] for b in info if b["wide"]]
    if is_left:
        right = min(w, max(wide_rx) + RIGHT_PAD) if wide_rx else w
    else:
        right = min(w, max(wide_rx) + RIGHT_PAD) if wide_rx else w

    return (top, bottom, left, right)


def doclayout_box(img: np.ndarray) -> tuple[int, int, int, int] | None:
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
    xs = [b["coordinate"][0] for b in body]
    ys = [b["coordinate"][1] for b in body]
    xe = [b["coordinate"][2] for b in body]
    ye = [b["coordinate"][3] for b in body]
    return (
        max(0, int(min(xs)) - 15),
        max(0, int(min(ys)) - 15),
        min(img.shape[1], int(max(xe)) + 15),
        min(img.shape[0], int(max(ye)) + 10),
    )


def main() -> None:
    pages = [int(x) for x in sys.argv[1:]] or [31, 33, 35, 37, 39]
    for pn in pages:
        p = SRC / f"page_{pn:04d}.png"
        if not p.exists():
            print(f"[skip] {p}")
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        h, w = img.shape[:2]
        raw = _detect_text_lines(img)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = _merge_nearby(filt, gap=8) if filt else []
        merged = sorted(merged, key=lambda b: b[1])
        new = new_rule(img, merged, w, h, pn)
        dl = doclayout_box(img)
        print("=" * 72)
        print(f"PAGE {pn:04d}  size={w}x{h}  (侧眉在{'左' if pn % 2 == 1 else '右'})")
        print(f"  新规则 (t,b,l,r)={new}  裁后={new[3]-new[2]}x{new[1]-new[0]}")
        if dl:
            print(f"  doclay (l,t,r,b)={dl}  裁后={dl[2]-dl[0]}x{dl[3]-dl[1]}")
            print(f"  偏差   l={new[2]-dl[0]:+d} r={new[3]-dl[2]:+d} t={new[0]-dl[1]:+d} b={new[1]-dl[3]:+d}")
        vis = Image.fromarray(img.copy())
        d = ImageDraw.Draw(vis)
        d.rectangle([new[2], new[0], new[3], new[1]], outline=(220, 0, 0), width=4)
        if dl:
            d.rectangle([dl[0], dl[1], dl[2], dl[3]], outline=(0, 200, 220), width=4)
        out = OUT / f"page_{pn:04d}_newrule.png"
        vis.save(out)
        print(f"  已保存: {out}  (红=新规则 / 青=doclayout)")


if __name__ == "__main__":
    main()

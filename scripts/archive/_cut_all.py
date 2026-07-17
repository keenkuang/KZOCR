"""临时脚本：把 22~992 页全部用 cv2 公式裁一遍，自动找异常页并出图。

最终公式（已用 doclayout 真值全量验证）：
  奇数页(侧眉在左)：
    left  = 用户差值公式：(mean(x1>15)-mean(x1全部))/2-15 + C(奇数105) —— 过裁 0
    right = w (整宽，右侧无边栏)
    top/bottom = 页眉页脚检测(left_page_rule)
  偶数页(侧眉在右)：
    left  = 用户差值公式：(mean(x1>15)-mean(x1全部))/2-15 + C(偶数75)
    right = user_right: M=max(x2); X=max(x2 where x2<=M-40); right=M-(M-X)/2-28
    top/bottom = 页眉页脚检测(user_top_bottom)
  注：(c) 侧眉感知方案已验证——激活即 34% 过裁，弃用。

异常判定（以 doclayout 正文框为权威基准）：
  doclayout 真值(可用时)：cv2 框切进正文(|偏差|>阈值 左右25/上下20) → 过裁
  doclayout 真值(可用时)：cv2 框留出侧眉(|偏差|超余量) → 欠裁
  公式自身逻辑：偶数 right>0.95w / M-X<20 → 侧眉残留

输出：
  crop_preview/all/summary.json   每页边界+标记
  crop_preview/all/page_NNNN_blocks.png   异常页 block 图(青=cv2 版心 红=裁切线 橙=doclayout)
用法：python _cut_all.py   (SKIP_DL=1 跳过 doclayout 加速)
"""
from __future__ import annotations

import json
import os
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
    _body_left_user,
    _LEFT_CALIB_ODD,
    _LEFT_CALIB_EVEN,
)
from _preview_even_formula import per_block, user_right, user_top_bottom
from _preview_odd_formula import left_page_rule

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_preview/all")
OUT.mkdir(parents=True, exist_ok=True)

WIDE_RATIO = 0.5
OVERCROP_PAD = 30        # 宽行超出 crop 框多少 px 算过裁
UNDERCROP_R = 0.95       # 偶数页 right > 0.95w 算侧眉残留
UNDERCROP_L = 0.04       # 奇数页 left  < 0.04w 算侧眉残留
GAP_MIN = 20             # 偶数页 M-X < 20 算无 gap
DEV_LR = 60              # doclayout 左右偏差阈值
DEV_TB = 40              # doclayout 上下偏差阈值

_MODEL = None


def doclayout_box(img):
    global _MODEL
    if _MODEL is None:
        _MODEL = _get_doclayout_model()
    if _MODEL is None:
        return None
    try:
        results = list(_MODEL.predict(img, batch_size=1))
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
    return (max(0, int(min(xs)) - 15), max(0, int(min(ys)) - 15),
            min(w, int(max(xe)) + 15), min(h, int(max(ye)) + 10))


def new_left_user(blocks, is_odd):
    """用户差值公式：left = (mean(x1>15) - mean(x1 全部))/2 - 15 + C。
    C 为每书标定常数（本书 奇数105/偶数75）。"""
    return _body_left_user(blocks, _LEFT_CALIB_ODD if is_odd else _LEFT_CALIB_EVEN)


def detect(img, page_num):
    h, w = img.shape[:2]
    raw = _detect_text_lines(img)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = _merge_nearby(filt, gap=8) if filt else []
    blocks = per_block(img, merged, w)
    is_odd = (page_num % 2 == 1)

    if is_odd:
        # 奇数页(侧眉在左)：left 用 (a)；right 保留整宽；top/bottom 用页眉页脚检测
        _l, top, _r, bottom = left_page_rule(blocks, h, w)
        left = new_left_user(blocks, is_odd)
        right = w
        M = X = None
    else:
        M, X, right, _ = user_right(blocks, w)
        right = min(w, max(0, right))
        left = new_left_user(blocks, is_odd)
        top, bottom = user_top_bottom(blocks, h)

    cw = right - left
    ch = bottom - top

    # 异常标记
    flags = []
    # 过裁/欠裁：以 doclayout 正文框为权威基准（已排除侧眉/页眉页脚）。
    # 仅当 cv2 框“切进” doclayout 正文时才算过裁；切掉侧眉是设计目的，不算。
    dl = doclayout_box(img) if not os.environ.get("SKIP_DL") else None
    if dl:
        dl_l, dl_t, dl_r, dl_b = dl
        if left > dl_l + 25:
            flags.append(f"过裁:左 cv2{left}>dl{dl_l}+25")
        if right < dl_r - 25:
            flags.append(f"过裁:右 cv2{right}<dl{dl_r}-25")
        if top > dl_t + 20:
            flags.append(f"过裁:上 cv2{top}>dl{dl_t}+20")
        if bottom < dl_b - 20:
            flags.append(f"过裁:下 cv2{bottom}<dl{dl_b}-20")
    # 欠裁（侧眉残留，公式自身逻辑）
    if is_odd:
        if left < w * UNDERCROP_L:
            flags.append(f"欠裁:奇数页left={left}<{w*UNDERCROP_L:.0f}(侧眉残留)")
    else:
        if right > w * UNDERCROP_R:
            flags.append(f"欠裁:偶数页right={right}>{w*UNDERCROP_R:.0f}w(侧眉残留)")
        if M is not None and (M - right) > 70:
            flags.append(f"侧眉残影:M-right={M-right}")
        if M is not None and X is not None and (M - X) < GAP_MIN:
            flags.append(f"无gap:M-X={M-X}(right退化=M)")
    # 裁幅过小
    if cw < w * 0.5:
        flags.append(f"裁幅过窄:cw={cw}")
    if ch < h * 0.5:
        flags.append(f"裁幅过短:ch={ch}")

    return dict(page=page_num, is_odd=is_odd, w=w, h=h,
                left=left, top=top, right=right, bottom=bottom,
                M=M, X=X, cw=cw, ch=ch, flags=flags, blocks=blocks,
                dl=list(dl) if dl else None)


def render(img, rec, dl):
    h, w = img.shape[:2]
    left, top, right, bottom = rec["left"], rec["top"], rec["right"], rec["bottom"]
    vis = Image.fromarray(img.copy())
    d = ImageDraw.Draw(vis)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for b in rec["blocks"]:
        color = (0, 200, 0) if b[4] > w * WIDE_RATIO else (230, 140, 0)
        d.rectangle([b[0], b[1], b[2], b[3]], outline=color, width=2)
        if font:
            d.text((b[0], max(0, b[1] - 12)), f"bw={b[4]}{'*' if b[4] > w*WIDE_RATIO else ''}",
                   fill=color, font=font)
    d.rectangle([left, top, right, bottom], outline=(0, 200, 220), width=4)
    d.line([right, 0, right, h], fill=(220, 0, 0), width=3)
    d.line([left, 0, left, h], fill=(220, 0, 0), width=3)
    if dl:
        d.rectangle([dl[0], dl[1], dl[2], dl[3]], outline=(255, 140, 0), width=3)
    vis.save(OUT / f"page_{rec['page']:04d}_blocks.png")
    cropped = img[top:bottom, left:right].copy()
    cropped = _post_trim_borders(cropped)
    Image.fromarray(cropped).save(OUT / f"page_{rec['page']:04d}_cropped.png")


def main() -> None:
    pages = list(range(22, 993))
    records = []
    problems = []
    done = 0
    for i, n in enumerate(pages):
        p = SRC / f"page_{n:04d}.png"
        if not p.exists():
            continue
        img = np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8)
        rec = detect(img, n)
        dl = rec.get("dl")
        if dl:
            rec["dev"] = dict(l=rec["left"] - dl[0], t=rec["top"] - dl[1],
                              r=rec["right"] - dl[2], b=rec["bottom"] - dl[3])
        if rec["flags"]:
            problems.append(rec)
            render(img, rec, dl)
        records.append({k: v for k, v in rec.items() if k != "blocks"})
        done += 1
        if (i + 1) % 100 == 0:
            print(f"[进度] {i+1}/{len(pages)} 已处理，当前异常 {len(problems)}", file=sys.stderr, flush=True)

    (OUT / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=" * 78, flush=True)
    print(f"总页数: {done}  异常页: {len(problems)}  ({len(problems)/max(1,done)*100:.1f}%)", flush=True)
    print("-" * 78, flush=True)
    print(f"{'页':>4} {'奇/偶':>4} {'left':>5} {'right':>6} {'top':>5} {'bottom':>6}  标记", flush=True)
    for r in problems:
        oe = "奇" if r["is_odd"] else "偶"
        print(f"{r['page']:>4} {oe:>4} {r['left']:>5} {r['right']:>6} {r['top']:>5} {r['bottom']:>6}  {'; '.join(r['flags'])}", flush=True)
    print(f"\n汇总: {OUT}/summary.json", flush=True)
    print(f"异常页图: {OUT}/page_XXXX_blocks.png", flush=True)


if __name__ == "__main__":
    main()

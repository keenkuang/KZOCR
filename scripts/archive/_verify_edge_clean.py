"""10px 自适应边裁原型验证（用户战略转向，2026-07-13）。

思路：进版面分析前，对原图四边各自判断「距边 10px 带内暗像素占比」，
有则裁 10px、无则不裁。目的不是裁侧眉/页眉本身，而是去掉贴边干扰
（竖边框线 x≈10–20、左上角页码桩 x≈4–15、扫描噪点），简化后续判断。

本脚本只验证原型效果，不渲染主流程：
1. 对每奇数页：原图跑 PP-DocLayoutV3 拿真值 dl_left / 正文左缘 / 侧眉右缘。
2. 跑 crop_edge_clean（band=10）得裁后图 + 四边触发标记，记录 left 裁掉量 lft。
3. 裁后图跑 cv2 文字行检测 → 简单紧框左界，两种候选：
   - simple   : min(块 x1) - 15   （最朴素）
   - skip_eb  : 跳过「窄左侧块」(宽<0.12w 且 x1<0.2w) 后 min(x1) - 15
4. 真值在裁后坐标：body_left_c = 正文左缘 - lft；eyebrow_r_c = 侧眉右缘 - lft。
5. 量：过裁(dl) / 过裁(正文) / 侧眉裁掉 / 侧眉残留；并回答两待核问题：
   - 暗度阈值 thr=128、触发占比 frac=0.02 是否稳健（四边触发率）。
   - 10px 是否盖住竖边框线：裁后左缘仍存「整列全黑列」的页数（若 >0 说明要放大 band）。
输出：crop_compare/edge_clean/ 下样本图 + 本脚本同目录 per_page json（供复核）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
OUT = Path("crop_compare/edge_clean")
OUT.mkdir(parents=True, exist_ok=True)

BAND = 10          # 边裁常数（候选，用户说 10px）
THR = 128          # 暗像素灰度阈值（排除 JPEG 噪点）
FRAC = 0.02        # 10px 带内暗像素占比阈值（单噪点不触发）
PAD = 15           # 简单紧框 padding（与 dl 一致）

BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
CV_LINE = (255, 30, 30)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)
SHADOW = (255, 255, 0)

_FONT = None
for _fp in (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = ImageFont.truetype(_fp, 20)
        except Exception:
            _FONT = None
        break


def crop_edge_clean(img: np.ndarray, band: int = BAND, thr: int = THR,
                    frac: float = FRAC):
    """四边自适应边裁：某边距边 band px 带内暗像素占比>frac 则裁 band px。

    返回 (cropped, crop_dict, border_persist)：
      crop_dict = {"top","bottom","left","right": 实际裁掉 px}
      border_persist = 裁后仍存在的「整列全黑列」最大 x（左缘附近），None 表示无残留。
    """
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    h, w = gray.shape[:2]
    crop = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    if h >= band and (gray[:band, :] < thr).mean() > frac:
        crop["top"] = band
    if h >= band and (gray[h - band:, :] < thr).mean() > frac:
        crop["bottom"] = band
    if w >= band and (gray[:, :band] < thr).mean() > frac:
        crop["left"] = band
    if w >= band and (gray[:, w - band:] < thr).mean() > frac:
        crop["right"] = band
    t, b, l, r = crop["top"], crop["bottom"], crop["left"], crop["right"]
    cropped = img[t: h - b if b else h, l: w - r if r else w]
    # 残留竖边框线检测：裁后左缘 20px 内整列全黑列的最大 x
    border_persist = None
    if cropped.shape[1] >= 1:
        cg = np.mean(cropped, axis=2) if cropped.ndim == 3 else cropped
        ch, cw = cg.shape
        for x in range(min(20, cw)):
            if np.mean(cg[:, x] < thr) > 0.9:
                border_persist = x
    return cropped, crop, border_persist


def simple_left(blocks, w, skip_eyebrow: bool = False):
    """简单紧框左界。skip_eyebrow=True 时跳过窄左侧块（侧眉）。"""
    if not blocks:
        return None
    if skip_eyebrow:
        body_blocks = [
            b for b in blocks
            if not (b[4] < 0.12 * w and b[0] < 0.2 * w)
        ]
        use = body_blocks if body_blocks else blocks
    else:
        use = blocks
    return int(max(0, min(b[0] for b in use) - PAD))


def render_sample(n, img, crop, simple_l, skip_l, dl_left, body_left, eyebrow_r,
                  lft, border_persist):
    pil = Image.fromarray(img.copy())
    h, w = img.shape[:2]
    MAX_DIM = 1600
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(pil)
    s = lambda v: int(round(v * scale))
    # 10px 裁掉带阴影
    for edge, px in crop.items():
        if not px:
            continue
        if edge == "left":
            d.rectangle([0, 0, s(px), s(h)], fill=SHADOW, outline=None)
        elif edge == "right":
            d.rectangle([s(w - px), 0, s(w), s(h)], fill=SHADOW, outline=None)
        elif edge == "top":
            d.rectangle([0, 0, s(w), s(px)], fill=SHADOW, outline=None)
        elif edge == "bottom":
            d.rectangle([0, s(h - px), s(w), s(h)], fill=SHADOW, outline=None)
    # 线：红=simple 蓝虚=skip 绿=dl 深蓝=正文左缘 橙=侧眉右缘
    if simple_l is not None:
        d.line([s(simple_l), 0, s(simple_l), h], fill=CV_LINE, width=3)
    if skip_l is not None:
        d.line([s(skip_l), 0, s(skip_l), h], fill=BODY_LINE, width=3)
    d.line([s(dl_left), 0, s(dl_left), h], fill=DL_LINE, width=2)
    d.line([s(body_left), 0, s(body_left), h], fill=(0, 0, 180), width=2)
    if eyebrow_r is not None:
        d.line([s(eyebrow_r), 0, s(eyebrow_r), h], fill=(255, 140, 0), width=2)
    legend = [
        f"p{n:04d} lft裁={lft}px 四边={crop}",
        f"simple={simple_l}  skip_eb={skip_l}  dl_left={dl_left}  正文左缘={body_left}  侧眉右缘={eyebrow_r}",
        f"裁后残留竖边框线最大x={border_persist}",
        "黄影=裁掉10px  红=simple 蓝=skip_eb 绿=dl 深蓝=正文左缘 橙=侧眉右缘",
    ]
    y = 6
    for line in legend:
        d.text((6, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=3,
               stroke_fill=(255, 255, 255))
        y += 26
    out = OUT / f"page_{n:04d}.png"
    pil.save(out)
    return out


def main() -> None:
    n_odd = 0
    dl_miss = 0
    edge_trig = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    simple_cut_dl, simple_cut_body = [], []
    skip_cut_dl, skip_cut_body = [], []
    simple_trim_ok = simple_trim_fail = skip_trim_ok = skip_trim_fail = 0
    no_eyebrow = 0
    border_persist_pages = []
    per_page = []
    samples = {"left_cropped": None, "left_notcropped": None, "border_persist": None}

    for n in range(22, 993):
        if n % 2 == 0 or not (SRC / f"page_{n:04d}.png").exists():
            continue
        n_odd += 1
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        h, w = img.shape[:2]
        dl = lc._doclayout_rect(img)
        if dl is None:
            dl_miss += 1
            continue
        dl_left = dl[0]
        body_left = dl_left + lc._DOC_LAYOUT_PAD_LR_T
        eyebrow_right = None
        try:
            res = list(lc._get_doclayout_model().predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
            se = [b["coordinate"][2] for b in boxes if b.get("label") == "aside_text"]
            if se:
                eyebrow_right = max(se)
        except Exception:
            pass

        cropped, crop, border_persist = crop_edge_clean(img)
        lft = crop["left"]
        for e in ("top", "bottom", "left", "right"):
            if crop[e]:
                edge_trig[e] += 1
        if border_persist is not None and lft:  # 裁左后仍残留整列黑线
            border_persist_pages.append((n, border_persist))

        # 裁后图 cv2 检测
        raw = lc._detect_text_lines(cropped)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        blocks = lc._compute_blocks(cropped, merged, cropped.shape[1]) if merged else []
        if not blocks:
            continue
        simple_l = simple_left(blocks, cropped.shape[1], skip_eyebrow=False)
        skip_l = simple_left(blocks, cropped.shape[1], skip_eyebrow=True)

        # 真值换算到裁后坐标
        body_left_c = body_left - lft
        dl_left_c = dl_left - lft
        eyebrow_r_c = (eyebrow_right - lft) if eyebrow_right is not None else None

        if simple_l is not None:
            if simple_l > dl_left_c:
                simple_cut_dl.append(n)
            if simple_l > body_left_c:
                simple_cut_body.append(n)
        if skip_l is not None:
            if skip_l > dl_left_c:
                skip_cut_dl.append(n)
            if skip_l > body_left_c:
                skip_cut_body.append(n)
        if eyebrow_r_c is not None:
            if simple_l is not None and simple_l > eyebrow_r_c:
                simple_trim_ok += 1
            else:
                simple_trim_fail += 1
            if skip_l is not None and skip_l > eyebrow_r_c:
                skip_trim_ok += 1
            else:
                skip_trim_fail += 1
        else:
            no_eyebrow += 1

        per_page.append({
            "n": n, "crop": crop, "lft": lft,
            "dl_left": dl_left, "body_left": body_left, "eyebrow_right": eyebrow_right,
            "simple_l": simple_l, "skip_l": skip_l,
            "border_persist": border_persist,
        })

        # 采样渲染
        if samples["left_cropped"] is None and crop["left"]:
            samples["left_cropped"] = (n, img.copy(), crop, simple_l, skip_l,
                                       dl_left, body_left, eyebrow_right, lft, border_persist)
        if samples["left_notcropped"] is None and not crop["left"]:
            samples["left_notcropped"] = (n, img.copy(), crop, simple_l, skip_l,
                                          dl_left, body_left, eyebrow_right, lft, border_persist)
        if samples["border_persist"] is None and border_persist is not None and lft:
            samples["border_persist"] = (n, img.copy(), crop, simple_l, skip_l,
                                          dl_left, body_left, eyebrow_right, lft, border_persist)

        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇数页  左裁触发={edge_trig['left']}  残留竖线="
                  f"{len(border_persist_pages)}", file=sys.stderr, flush=True)

    if samples["left_cropped"]:
        out = render_sample(*samples["left_cropped"])
        print(f"[样本] 左缘有干扰(裁左): {out}")
    if samples["left_notcropped"]:
        out = render_sample(*samples["left_notcropped"])
        print(f"[样本] 左缘无干扰(不裁左): {out}")
    if samples["border_persist"]:
        out = render_sample(*samples["border_persist"])
        print(f"[样本] 裁左后仍残留竖线: {out}")

    (Path(__file__).parent / "edge_clean_per_page.json").write_text(
        json.dumps(per_page, ensure_ascii=False))

    print(f"\n=== 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"[边裁触发率] {edge_trig}  (占奇数页比例: "
          f"top={edge_trig['top']/n_odd:.1%} bottom={edge_trig['bottom']/n_odd:.1%} "
          f"left={edge_trig['left']/n_odd:.1%} right={edge_trig['right']/n_odd:.1%})")
    print(f"[10px 是否盖住竖边框线] 裁左后仍残留整列黑线的页数={len(border_persist_pages)}"
          f"  {border_persist_pages[:10]}")
    print(f"\n--- simple (最朴素 min x1 - 15) ---")
    print(f"过裁(dl)={len(simple_cut_dl)}  过裁(正文)={len(simple_cut_body)}")
    print(f"侧眉裁掉={simple_trim_ok}  残留={simple_trim_fail}  无侧眉(dl)={no_eyebrow}")
    print(f"\n--- skip_eb (跳过窄左侧块) ---")
    print(f"过裁(dl)={len(skip_cut_dl)}  过裁(正文)={len(skip_cut_body)}")
    print(f"侧眉裁掉={skip_trim_ok}  残留={skip_trim_fail}")
    print(f"\n[v4b 对照] 过裁(dl)=104  过裁(正文)=5  侧眉裁掉=455 残留=11")


if __name__ == "__main__":
    main()

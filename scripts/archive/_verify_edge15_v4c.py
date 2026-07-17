"""边裁 band=15 集成管线全量验证（用户 2026-07-14 建议：10→15）。

管线：原图 → crop_edge_clean(band=15) 预处理 → 裁后图算块 → v4c 左界。
dl 真值在裁后坐标：dl_left_c = dl_left_o - lft；body_left_c = body_left_o - lft；
eyebrow_r_c = eyebrow_r_o - lft（dl 跑原图，按 lft 偏移，跨 band 一致）。

统计：
  - 边裁触发率(band=15)
  - 过裁(dl)    : v4c_left > dl_left_c        （宽松，含 15px pad 内）
  - 过裁(正文)  : v4c_left > body_left_c      （严格，真切墨）
  - 侧眉裁掉/残留: v4c_left > eyebrow_r_c ?
渲染问题页：
  - overcut_dl/    : left > dl_left 的页
  - eyebrow_resid/ : left <= eyebrow_r 的页（侧眉欠裁）
输出 per_page json 供复核。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _verify_edge_clean import crop_edge_clean, THR, FRAC
from _verify_v4c import user_odd_left_v4c

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
BAND = 15
OUT = Path("crop_compare/edge15_v4c")
OUT_OC = OUT / "overcut_dl"
OUT_RES = OUT / "eyebrow_resid"
for d in (OUT_OC, OUT_RES):
    d.mkdir(parents=True, exist_ok=True)

BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
CV_LINE = (255, 30, 30)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)
EB_LINE = (255, 140, 0)
CUT_BLOCK = (255, 0, 0)

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


def render_problem(n, img, blocks, v4c_l, dl_left_o, body_left_o, eyebrow_r_o,
                   lft, branch, kind):
    h, w = img.shape[:2]
    pil = Image.fromarray(img.copy())
    MAX_DIM = 1600
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(pil)
    s = lambda v: int(round(v * scale))
    # v4c 线在原始坐标 = v4c_l(裁后) + lft
    vc_orig = (v4c_l + lft) if v4c_l is not None else None
    wide_th = w * 0.5
    n_cut = 0
    for (lx, y1, rx, y2, bw) in blocks:
        color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
        d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=1)
        if vc_orig is not None and lx < vc_orig < rx:
            d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=CUT_BLOCK, width=3)
            n_cut += 1
    if vc_orig is not None:
        d.line([s(vc_orig), 0, s(vc_orig), h], fill=CV_LINE, width=3)
    d.line([s(dl_left_o), 0, s(dl_left_o), h], fill=DL_LINE, width=2)
    d.line([s(body_left_o), 0, s(body_left_o), h], fill=BODY_LINE, width=2)
    if eyebrow_r_o is not None:
        d.line([s(eyebrow_r_o), 0, s(eyebrow_r_o), h], fill=EB_LINE, width=2)
    oc = (v4c_l > (dl_left_o - lft)) if v4c_l is not None else False
    resid = (eyebrow_r_o is not None and v4c_l is not None and v4c_l <= (eyebrow_r_o - lft))
    legend = [
        f"p{n:04d} 奇  v4c左(原)={vc_orig}  dl_left={dl_left_o}  正文左缘={body_left_o}  侧眉右缘={eyebrow_r_o}",
        f"分支={branch}  lft裁={lft}  过裁(dl)={'YES' if oc else 'no'}  侧眉欠裁={'YES' if resid else 'no'}  被切行={n_cut}",
        "绿/橙=块 红框=被v4c切中 红=v4c 绿=dl 蓝=正文左缘 橙=侧眉右缘",
    ]
    y = 6
    for line in legend:
        d.text((6, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=3,
               stroke_fill=(255, 255, 255))
        y += 26
    sub = OUT_OC if oc else OUT_RES
    out = sub / f"page_{n:04d}.png"
    pil.save(out)
    return out, oc, resid, n_cut


def main() -> None:
    n_odd = dl_miss = 0
    edge_trig = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    oc_dl = []          # left > dl_left
    oc_body = []        # left > body_left
    resid = []          # left <= eyebrow_r (欠裁)
    trimmed = 0
    no_eb = 0
    branches = {}
    per_page = []
    rendered_oc = rendered_res = 0

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
        dl_left_o = dl[0]
        body_left_o = dl_left_o + lc._DOC_LAYOUT_PAD_LR_T
        eyebrow_r_o = None
        try:
            res = list(lc._get_doclayout_model().predict(img, batch_size=1))
            boxes = lc._extract_doclayout_boxes(res[0])
            se = [b["coordinate"][2] for b in boxes if b.get("label") == "aside_text"]
            if se:
                eyebrow_r_o = max(se)
        except Exception:
            pass

        cropped, crop, bp = crop_edge_clean(img, band=BAND)
        lft = crop["left"]
        for e in ("top", "bottom", "left", "right"):
            if crop[e]:
                edge_trig[e] += 1
        cw, ch = cropped.shape[1], cropped.shape[0]
        raw = lc._detect_text_lines(cropped)
        filt = [b for b in raw if b[3] - b[1] >= 8]
        merged = lc._merge_nearby(filt, gap=8) if filt else []
        blocks_clean = lc._compute_blocks(cropped, merged, cw) if merged else []
        if not blocks_clean:
            continue
        lv4, tup = user_odd_left_v4c(blocks_clean, cw, ch)
        branch = tup[-1] if tup else "?"
        if lv4 is None:
            continue
        branches[branch] = branches.get(branch, 0) + 1

        dl_left_c = dl_left_o - lft
        body_left_c = body_left_o - lft
        eb_c = (eyebrow_r_o - lft) if eyebrow_r_o is not None else None

        is_oc = lv4 > dl_left_c
        if is_oc:
            oc_dl.append(n)
        if lv4 > body_left_c:
            oc_body.append(n)
        if eb_c is not None:
            if lv4 > eb_c:
                trimmed += 1
            else:
                resid.append(n)
        else:
            no_eb += 1

        per_page.append({"n": n, "lft": lft, "v4c_l": lv4, "branch": branch,
                         "dl_left": dl_left_o, "body_left": body_left_o,
                         "eyebrow_r": eyebrow_r_o,
                         "oc": is_oc, "resid": (eb_c is not None and lv4 <= eb_c)})

        # 渲染问题页
        if is_oc or (eb_c is not None and lv4 <= eb_c):
            out, _, _, _ = render_problem(n, img, blocks_clean, lv4, dl_left_o,
                                          body_left_o, eyebrow_r_o, lft, branch,
                                          "oc" if is_oc else "resid")
            if is_oc:
                rendered_oc += 1
            else:
                rendered_res += 1

        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇  左裁={edge_trig['left']}  "
                  f"过裁(dl)={len(oc_dl)} 欠裁={len(resid)} 渲染={rendered_oc+rendered_res}",
                  file=sys.stderr, flush=True)

    (Path(__file__).parent / "edge15_v4c_per_page.json").write_text(
        json.dumps(per_page, ensure_ascii=False))

    print(f"\n=== band={BAND} 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
    print(f"[边裁触发] {edge_trig}")
    print(f"[分支] {branches}")
    print(f"\n--- 问题统计表 ---")
    print(f"{'指标':<14}{'页数':>6}{'占比':>8}")
    print(f"{'过裁(dl)':<14}{len(oc_dl):>6}{len(oc_dl)/n_odd:>7.1%}")
    print(f"{'过裁(正文)':<14}{len(oc_body):>6}{len(oc_body)/n_odd:>7.1%}")
    print(f"{'侧眉欠裁':<14}{len(resid):>6}{len(resid)/n_odd:>7.1%}")
    print(f"{'侧眉裁掉':<14}{trimmed:>6}{trimmed/n_odd:>7.1%}")
    print(f"无侧眉(dl)={no_eb}")
    print(f"\n过裁(dl) 页={oc_dl}")
    print(f"过裁(正文) 页={oc_body}")
    print(f"侧眉欠裁 页={resid}")
    print(f"\n[v4c band=10 对照] 过裁(dl)=85 过裁(正文)=1 侧眉裁掉=447 残留=19")
    print(f"[渲染] overcut_dl={rendered_oc} 页 → {OUT_OC}")
    print(f"[渲染] eyebrow_resid={rendered_res} 页 → {OUT_RES}")


if __name__ == "__main__":
    main()

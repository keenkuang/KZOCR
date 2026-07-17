"""v5：band=15 边裁 + 用户新左界公式（2026-07-14）。

用户新公式（裁后坐标）：
  if A 存在且 A<B: left = min(A-10, B-50)
  elif A 存在且 B<A: left = max(A-10, B+30)
  if A 不存在: left = max(B-30, C+70)

ABCD 仍按旧定义计算（仅用于显示/复核）：
  A = min(x1 | x1>0.05w, y1<0.06h, y2<0.15h)
  B = min(x1 | x1>0.07w, 0.15h<y1<0.85h)
  C = mean(x1 | 0.02w<x1<0.06w, 0.15h<y1<0.85h)
  D = min(x1 | x1<0.05w, y1<0.06h, y2<0.15h)

两阶段：
  compute : 全量 485 奇数页 → edge15_v5_per_page.json（含 ABCD、left、lft、真值）
  render  : 读 json，对问题页（过裁/侧眉欠裁）重画渲染图，左下角标注 ABCD。
真值在裁后坐标：dl_left_c = dl_left_o - lft；body_left_c = body_left_o - lft；
eyebrow_r_c = eyebrow_r_o - lft。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
BAND = 15
OUT = Path("crop_compare/edge15_v6")
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
_FONT_SIZE = 30
for _fp in (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",          # 文泉驿正黑（中英文）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Noto CJK
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",  # Droid 回退
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = ImageFont.truetype(_fp, _FONT_SIZE)
        except Exception:
            _FONT = None
        break


def user_odd_left_v6(blocks, w, h):
    """v6 公式（用户 2026-07-14）：
      A<=B : left = min(A-10, B-60, C+60)
      B<A  : left = min(B-10, C+60)
      无A  : left = min(B-30, C+80, (D+B)/2-15)
    C+60/C+80 作 min 下界项，防止越过正文左缘。返回 (left, (A,B,C,D,branch))。
    """
    A = B = C = D = None
    A_vals, B_vals, C_vals, D_vals = [], [], [], []
    for (x1, y1, x2, y2, bw) in blocks:
        if y1 < 0.06 * h and y2 < 0.15 * h:
            if x1 > 0.05 * w:
                A_vals.append(x1)
            elif x1 < 0.05 * w:
                D_vals.append(x1)
        if 0.07 * w < x1 < 0.12 * w and 0.5 * h < y1 < 0.85 * h:
            B_vals.append(x1)
        if 0.02 * w < x1 < 0.06 * w and 0.15 * h < y1 < 0.85 * h:
            C_vals.append(x1)
    # 兜底：新B区无块时，回退旧B定义（x1>0.07w, 0.15h<y1<0.85h）
    if not B_vals:
        for (x1, y1, x2, y2, bw) in blocks:
            if x1 > 0.07 * w and 0.15 * h < y1 < 0.85 * h:
                B_vals.append(x1)
    if B_vals:
        B = min(B_vals)
    if C_vals:
        C = sum(C_vals) / len(C_vals)
    if A_vals:
        A = min(A_vals)
    if D_vals:
        D = min(D_vals)
    if B is None:
        return None, (A, B, C, D, "?")

    if A is not None:
        if A <= B:
            terms = [A - 10, B - 60]
            if C is not None:
                terms.append(C + 60)
            left = min(terms)
            branch = "A<=B"
        else:
            terms = [B - 10]
            if C is not None:
                terms.append(C + 60)
            left = min(terms)
            branch = "B<A"
    else:
        terms = [B - 30]
        if C is not None:
            terms.append(C + 80)
        if D is not None:
            terms.append((D + B) / 2 - 15)
        left = min(terms)
        branch = "noA"
    return int(left), (A, B, C, D, branch)


def run_compute() -> None:
    n_odd = dl_miss = 0
    edge_trig = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    oc_dl, oc_body, resid = [], [], []
    trimmed = no_eb = 0
    branches = {}
    per_page = []

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

        cropped, crop, _ = crop_edge_clean(img, band=BAND)
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
        lv6, tup = user_odd_left_v6(blocks_clean, cw, ch)
        A, B, C, D, branch = tup
        if lv6 is None:
            continue
        branches[branch] = branches.get(branch, 0) + 1

        dl_left_c = dl_left_o - lft
        body_left_c = body_left_o - lft
        eb_c = (eyebrow_r_o - lft) if eyebrow_r_o is not None else None

        is_oc = lv6 > dl_left_c
        if is_oc:
            oc_dl.append(n)
        if lv6 > body_left_c:
            oc_body.append(n)
        if eb_c is not None:
            if lv6 > eb_c:
                trimmed += 1
            else:
                resid.append(n)
        else:
            no_eb += 1

        per_page.append({"n": n, "lft": lft,
                         "v5_l": lv6, "A": A, "B": B,
                         "C": (None if C is None else round(C, 1)),
                         "D": D, "branch": branch,
                         "dl_left": dl_left_o, "body_left": body_left_o,
                         "eyebrow_r": eyebrow_r_o,
                         "oc": is_oc, "cut_body": lv6 > body_left_c,
                         "resid": (eb_c is not None and lv6 <= eb_c)})

        if n_odd % 100 == 0:
            print(f"[进度] {n_odd} 奇  左裁={edge_trig['left']}  "
                  f"过裁(dl)={len(oc_dl)} 过裁(正文)={len(oc_body)} 欠裁={len(resid)}",
                  file=sys.stderr, flush=True)

    (Path(__file__).parent / "edge15_v6_per_page.json").write_text(
        json.dumps(per_page, ensure_ascii=False))

    print(f"\n=== band={BAND} v6 完成 奇数页={n_odd}  dl缺失={dl_miss} ===")
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
    print(f"[v4c band=15 对照] 过裁(dl)=23 过裁(正文)=6 侧眉裁掉=446 残留=11")


FORMULA = {
    "A<=B": "min(A-10,B-60,C+60)",
    "B<A": "min(B-10,C+60)",
    "noA": "min(B-30,C+80,(D+B)/2-15)",
}


def _load_blocks(n):
    img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                     dtype=np.uint8)
    cropped, crop, _ = crop_edge_clean(img, band=BAND)
    lft = crop["left"]
    cw, ch = cropped.shape[1], cropped.shape[0]
    raw = lc._detect_text_lines(cropped)
    filt = [b for b in raw if b[3] - b[1] >= 8]
    merged = lc._merge_nearby(filt, gap=8) if filt else []
    blocks = lc._compute_blocks(cropped, merged, cw) if merged else []
    return img, cropped, lft, blocks, cw, ch


def render_problem(rec) -> None:
    n = rec["n"]
    img, cropped, lft, blocks, cw, ch = _load_blocks(n)
    h, w = img.shape[:2]
    pil = Image.fromarray(img.copy())
    MAX_DIM = 1600
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(pil)
    s = lambda v: int(round(v * scale))
    v5_l = rec["v5_l"]
    vc_orig = v5_l + lft
    dl_left_o = rec["dl_left"]
    body_left_o = rec["body_left"]
    eyebrow_r_o = rec["eyebrow_r"]

    wide_th = w * 0.5
    n_cut = 0
    for (lx, y1, rx, y2, bw) in blocks:
        color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
        d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=4)
        if lx < vc_orig < rx:
            d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=CUT_BLOCK, width=7)
            n_cut += 1
    d.line([s(vc_orig), 0, s(vc_orig), h], fill=CV_LINE, width=5)
    d.line([s(dl_left_o), 0, s(dl_left_o), h], fill=DL_LINE, width=3)
    d.line([s(body_left_o), 0, s(body_left_o), h], fill=BODY_LINE, width=3)
    if eyebrow_r_o is not None:
        d.line([s(eyebrow_r_o), 0, s(eyebrow_r_o), h], fill=EB_LINE, width=3)

    C = rec["C"]
    C_txt = (f"{C:.0f}" if C is not None else "None")
    lines = [
        f"p{n:04d} 奇  lft裁={lft}  左(原)={vc_orig}",
        f"dl_left={dl_left_o}  正文左缘={body_left_o}  侧眉右缘={eyebrow_r_o}",
        f"A={rec['A']}  B={rec['B']}  C={C_txt}  D={rec['D']}  分支={rec['branch']}:{FORMULA.get(rec['branch'], '?')}",
        f"过裁(dl)={'YES' if rec['oc'] else 'no'}  过裁(正文)={'YES' if rec['cut_body'] else 'no'}  侧眉欠裁={'YES' if rec['resid'] else 'no'}  被切行={n_cut}",
        "绿/橙=块 红框=被切 红=左界 绿=dl 蓝=正文 橙=侧眉",
    ]
    lh = 40
    total_h = lh * len(lines)
    y = int(pil.height - total_h - 8)
    for line in lines:
        d.text((8, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=4,
               stroke_fill=(255, 255, 255))
        y += lh

    if rec["oc"]:
        out = OUT_OC / f"page_{n:04d}.png"
    else:
        out = OUT_RES / f"page_{n:04d}.png"
    pil.save(out)


def run_render() -> None:
    data = json.loads((Path(__file__).parent / "edge15_v6_per_page.json").read_text())
    prob = [r for r in data if r["oc"] or r["resid"]]
    for r in prob:
        render_problem(r)
    print(f"[渲染] 问题页={len(prob)} → {OUT_OC} / {OUT_RES}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("compute", "all"):
        run_compute()
    if mode in ("render", "all"):
        if not (Path(__file__).parent / "edge15_v5_per_page.json").exists():
            print("缺少 per_page json，请先 compute", file=sys.stderr)
            return
        run_render()


if __name__ == "__main__":
    main()

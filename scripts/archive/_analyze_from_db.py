"""从主数据库 crop_master.json 秒级评估任意左界公式（不再跑 dl/cv2）。

用法：
  python _analyze_from_db.py v6            # 仅统计 + 各分支 ABCD/页码表
  python _analyze_from_db.py v6 render     # 统计 + 渲染问题页（用库内块，秒级）
  python _analyze_from_db.py v4c           # 对比其他公式

公式函数返回 (left, (A,B,C,D,branch))，块坐标取自库（裁后坐标）。
真值在裁后坐标：dl_left_c=dl_left-lft; body_left_c=body_left-lft; eb_r_c=eyebrow.right-lft。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from _verify_edge15_v5 import user_odd_left_v6, FORMULA
from _verify_v4c import user_odd_left_v4c

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
DB = Path(__file__).parent / "crop_master.json"

FORMULAS = {
    "v6": user_odd_left_v6,
    "v4c": user_odd_left_v4c,
}

_FONT = None
for _fp in (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
):
    if Path(_fp).exists():
        try:
            _FONT = ImageFont.truetype(_fp, 30)
        except Exception:
            _FONT = None
        break

BLOCK_WIDE = (0, 170, 60)
BLOCK_NARROW = (255, 140, 0)
CV_LINE = (255, 30, 30)
DL_LINE = (20, 200, 20)
BODY_LINE = (30, 120, 255)
EB_LINE = (255, 140, 0)
CUT_BLOCK = (255, 0, 0)


def evaluate(db, fn):
    rows = []
    for p in db:
        blocks = [tuple(b) for b in p["cv2_blocks"]]
        cw, ch = p["cw"], p["ch"]
        res = fn(blocks, cw, ch)
        if res is None or res[0] is None:
            continue
        left, tup = res
        A, B, C, D, branch = tup
        lft = p["lft"]
        dl_left = p["dl_left"]
        body_left = p["body_left"]
        eb = p["eyebrow"]
        eb_r = eb["right"]
        dl_c = (dl_left - lft) if dl_left is not None else None
        body_c = (body_left - lft) if body_left is not None else None
        eb_c = (eb_r - lft) if eb["n_boxes"] else None
        is_oc = (dl_c is not None and left > dl_c)
        cut_body = (body_c is not None and left > body_c)
        resid = (eb_c is not None and left <= eb_c)
        rows.append({"n": p["n"], "lft": lft, "left": left, "branch": branch,
                     "A": A, "B": B, "C": C, "D": D,
                     "dl_left": dl_left, "body_left": body_left, "eyebrow_r": eb_r,
                     "oc": is_oc, "cut_body": cut_body, "resid": resid})
    return rows


def print_stats(rows, name):
    n = len(rows)
    oc = [r["n"] for r in rows if r["oc"]]
    cb = [r["n"] for r in rows if r["cut_body"]]
    resid = [r["n"] for r in rows if r["resid"]]
    trim = n - len(resid)
    print(f"\n=== {name} 统计（{n} 页）===")
    print(f"{'过裁(dl)':<12}{len(oc):>6}{len(oc)/n:>8.1%}")
    print(f"{'过裁(正文)':<12}{len(cb):>6}{len(cb)/n:>8.1%}")
    print(f"{'侧眉欠裁':<12}{len(resid):>6}{len(resid)/n:>8.1%}")
    print(f"{'侧眉裁掉':<12}{trim:>6}{trim/n:>8.1%}")
    print(f"过裁(正文)页={cb}")
    print(f"侧眉欠裁页={resid}")


def print_branch_table(rows):
    branches = sorted({r["branch"] for r in rows})
    for br in branches:
        rs = [r for r in rows if r["branch"] == br]
        A = [r["A"] for r in rs]
        B = [r["B"] for r in rs]
        C = [r["C"] for r in rs]
        D = [r["D"] for r in rs]

        def rng(v):
            v = [x for x in v if x is not None]
            return "None" if not v else f"[{min(v):.0f},{max(v):.0f}](n={len(v)})"
        prob = [r for r in rs if r["oc"] or r["resid"]]
        oc = [r["n"] for r in prob if r["oc"]]
        cb = [r["n"] for r in prob if r["cut_body"]]
        resid = [r["n"] for r in prob if r["resid"]]
        print(f"\n--- 分支 {br}（共 {len(rs)} 页，问题 {len(prob)}）---")
        print(f"  A={rng(A)}  B={rng(B)}  C={rng(C)}  D={rng(D)}")
        print(f"  过裁(dl)={len(oc)} 过裁(正文)={len(cb)} 侧眉欠裁={len(resid)}")
        if prob:
            print(f"  问题页: {sorted(r['n'] for r in prob)}")


def render(rows, formula_name, db):
    out = Path(f"crop_compare/{formula_name}")
    oc_dir = out / "overcut_dl"
    res_dir = out / "eyebrow_resid"
    oc_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)
    by_n = {p["n"]: p for p in db}
    cnt = 0
    for r in rows:
        if not (r["oc"] or r["resid"]):
            continue
        n = r["n"]
        p = by_n.get(n)
        if p is None:
            continue
        blocks = [tuple(b) for b in p["cv2_blocks"]]
        img = np.asarray(Image.open(SRC / f"page_{n:04d}.png").convert("RGB"),
                         dtype=np.uint8)
        h, w = img.shape[:2]
        pil = Image.fromarray(img.copy())
        MAX_DIM = 1600
        scale = min(1.0, MAX_DIM / max(w, h))
        if scale < 1.0:
            pil = pil.resize((int(w * scale), int(h * scale)))
        d = ImageDraw.Draw(pil)
        s = lambda v: int(round(v * scale))
        vc_orig = r["left"] + r["lft"]
        wide_th = w * 0.5
        n_cut = 0
        for (lx, y1, rx, y2, bw) in blocks:
            color = BLOCK_WIDE if bw > wide_th else BLOCK_NARROW
            d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=color, width=4)
            if lx < vc_orig < rx:
                d.rectangle([s(lx), s(y1), s(rx), s(y2)], outline=CUT_BLOCK, width=7)
                n_cut += 1
        d.line([s(vc_orig), 0, s(vc_orig), h], fill=CV_LINE, width=5)
        d.line([s(r["dl_left"]), 0, s(r["dl_left"]), h], fill=DL_LINE, width=3)
        d.line([s(r["body_left"]), 0, s(r["body_left"]), h], fill=BODY_LINE, width=3)
        if r["eyebrow_r"] is not None:
            d.line([s(r["eyebrow_r"]), 0, s(r["eyebrow_r"]), h], fill=EB_LINE, width=3)
        C = r["C"]
        C_txt = (f"{C:.0f}" if C is not None else "None")
        lines = [
            f"p{n:04d} 奇  lft裁={r['lft']}  左(原)={vc_orig}  被切行={n_cut}",
            f"dl_left={r['dl_left']}  正文左缘={r['body_left']}  侧眉右缘={r['eyebrow_r']}",
            f"A={r['A']}  B={r['B']}  C={C_txt}  D={r['D']}  分支={r['branch']}:{FORMULA.get(r['branch'],'?')}",
            f"过裁(dl)={'YES' if r['oc'] else 'no'}  过裁(正文)={'YES' if r['cut_body'] else 'no'}  侧眉欠裁={'YES' if r['resid'] else 'no'}",
            "绿/橙=块 红框=被切 红=左界 绿=dl 蓝=正文 橙=侧眉",
        ]
        lh = 40
        y = int(pil.height - lh * len(lines) - 8)
        for line in lines:
            d.text((8, y), line, fill=(0, 0, 0), font=_FONT, stroke_width=4,
                   stroke_fill=(255, 255, 255))
            y += lh
        target = oc_dir if r["oc"] else res_dir
        pil.save(target / f"page_{n:04d}.png")
        cnt += 1
    print(f"[渲染] {formula_name} 问题页={cnt} → {out}/overcut_dl,eyebrow_resid")


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "v6"
    do_render = "render" in sys.argv[2:]
    db = json.loads(DB.read_text())
    fn = FORMULAS[name]
    rows = evaluate(db, fn)
    print_stats(rows, name)
    print_branch_table(rows)
    if do_render:
        render(rows, name, db)


if __name__ == "__main__":
    main()

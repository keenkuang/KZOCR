"""渲染 inpaint+重调v6 仍欠裁的页，目视侧眉为何残留。
红=重调v6裁切线  蓝=原v6裁切线  橙=侧眉框  绿虚线=正文左缘(body_left)。
只渲染左侧 400px 细看侧眉区。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kzocr.engine import layout_crop as lc
from _verify_edge_clean import crop_edge_clean

SRC = Path("/home/keen/Documents/OCR0625/mi-by-ppocrv6/images")
DB = json.loads(open("crop_master_inpaint.json").read())
OUT = Path("crop_compare/inpaint_undertrim")
OUT.mkdir(parents=True, exist_ok=True)

COEF = {"a1": 0, "b1": 40, "c1": 40, "b2": 10, "c2": 40,
        "b3": 50, "c3": 60, "d1": 15, "c4": 40}
V6 = {"a1": 10, "b1": 60, "c1": 60, "b2": 10, "c2": 60,
      "b3": 30, "c3": 80, "d1": 15, "c4": 60}


def f_detail(A, B, C, D, c):
    """返回 (左裁值, 分支标签, 公式串)，公式串含代入数值与结果。"""
    if A is not None and B is not None:
        if A <= B:
            terms = [("A-a1", A - c["a1"]), ("B-b1", B - c["b1"])]
            if C is not None:
                terms.append(("C+c1", C + c["c1"]))
            label = "A<=B"
        else:
            terms = [("B-b2", B - c["b2"])]
            if C is not None:
                terms.append(("C+c2", C + c["c2"]))
            label = "B<A"
    elif B is not None:
        terms = [("B-b3", B - c["b3"])]
        if C is not None:
            terms.append(("C+c3", C + c["c3"]))
        if D is not None:
            terms.append(("(D+B)/2-d1", (D + B) / 2 - c["d1"]))
        label = "noA"
    elif C is not None:
        terms = [("C+c4", C + c["c4"])]
        label = "onlyC"
    else:
        return None, "无", ""
    val = min(t[1] for t in terms)
    sym = ",".join(t[0] for t in terms)
    nums = ",".join(f"{t[1]:.0f}" for t in terms)
    expr = f"min({sym})=min({nums})={val:.0f}"
    return val, label, expr


def eyebrow_right(p):
    ey = p.get("eyebrow") or {}
    r = ey.get("right")
    return int(r) if r is not None else None


def formula_table(c, name):
    """整张 left 公式表（系数形式），四分支全列。"""
    return [
        name,
        f"A<=B : min(A-{c['a1']}, B-{c['b1']}, C+{c['c1']})",
        f"B<A  : min(B-{c['b2']}, C+{c['c2']})",
        f"noA  : min(B-{c['b3']}, C+{c['c3']}, (D+B)/2-{c['d1']})",
        f"onlyC: C+{c['c4']}",
    ]


def nf(v):
    return "无" if v is None else f"{v:.0f}"


# 找欠裁页
under = []
for p in DB:
    A, B, C, D, _ = p["abcd_inp"]
    lv, _, _ = f_detail(A, B, C, D, COEF)
    if lv is None:
        continue
    lv_orig = lv + p["crop_left"]
    er = eyebrow_right(p)
    if er is not None and er > lv_orig:
        under.append(p["n"])
under_set = set(under)
print(f"重调v6 仍欠裁页数: {len(under)} -> {under}")


def render(n):
    p = next(x for x in DB if x["n"] == n)
    img = Image.open(SRC / f"page_{n:04d}.png").convert("RGB")
    cropped, crop_info, _ = crop_edge_clean(np.asarray(img), band=15)
    cl = int(crop_info.get("left", 0))
    ct = int(crop_info.get("top", 0))
    W, H = img.size

    # 重调v6 (inpainted)
    A, B, C, D, _ = p["abcd_inp"]
    lv_ret, br_label, expr_ret = f_detail(A, B, C, D, COEF)
    lv_ret_o = (lv_ret + cl) if lv_ret is not None else None
    # 原v6 (raw)
    A0, B0, C0, D0, _ = p["abcd_raw"]
    lv_v6, _, expr_v6 = f_detail(A0, B0, C0, D0, V6)
    lv_v6_o = (lv_v6 + cl) if lv_v6 is not None else None

    draw = ImageDraw.Draw(img)

    def vline(x, color, dash=False):
        if x is None:
            return
        x = int(round(x))
        if dash:
            for y in range(0, H, 24):
                draw.line([(x, y), (x, min(y + 12, H - 1))], fill=color, width=4)
        else:
            draw.line([(x, 0), (x, H - 1)], fill=color, width=4)

    # 侧眉框
    ey = p.get("eyebrow") or {}
    for b in ey.get("boxes") or []:
        x1, y1, x2, y2 = b[:4]
        draw.rectangle([x1, y1, x2, y2], outline=(0, 165, 255), width=2)

    # cv2 检测块（cropped 坐标 → 原图：x+cl, y+ct）
    def draw_blocks(blocks, color, width=1):
        for b in blocks:
            x1, y1, x2, y2 = b[0], b[1], b[2], b[3]
            draw.rectangle([x1 + cl, y1 + ct, x2 + cl, y2 + ct],
                           outline=color, width=width)
    if p.get("triggered"):
        draw_blocks(p["blocks_raw"], (150, 150, 150))   # 灰 raw（inpaint 前）
    draw_blocks(p["blocks_inp"], (0, 200, 255))         # 青 inp（重调v6 用）

    vline(lv_ret_o, (255, 0, 0))            # 红 重调v6
    vline(lv_v6_o, (0, 0, 255))            # 蓝 原v6
    vline(p.get("body_left"), (0, 170, 0), dash=True)  # 绿虚线 正文左缘

    # 整张 left 公式表（左上角，红=重调v6 / 蓝=原v6）
    fntf = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 24)
    tbl_ret = formula_table(COEF, "【重调v6 左公式】")
    tbl_v6 = formula_table(V6, "【原v6 左公式】")
    all_lines = tbl_ret + tbl_v6
    flh, fpad = 30, 6
    fbw = max(draw.textlength(s, font=fntf) for s in all_lines) + 2 * fpad
    fbh = len(all_lines) * flh + 2 * fpad
    fx, fy = max(10, W - fbw - 10), 10
    draw.rectangle([fx, fy, fx + fbw, fy + fbh], fill=(255, 255, 255, 215))
    for i, s in enumerate(all_lines):
        color = (255, 0, 0) if i < len(tbl_ret) else (0, 0, 255)
        if s.startswith("【"):
            color = (0, 0, 0)
        draw.text((fx + fpad, fy + fpad + i * flh), s, font=fntf, fill=color)

    # 图例（左下角，文泉驿正黑）
    fnt = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 30)
    A, B, C, D, _ = p["abcd_inp"]
    br = br_label
    lines = [
        f"p{n} {'仍欠裁' if n in under_set else 'D存在页'}  分支={br}",
        f"A={nf(A)}  B={nf(B)}  C={nf(C)}  D={nf(D)}",
        f"红公式={expr_ret}",
        f"蓝公式={expr_v6}",
        f"红=重调v6左={lv_ret_o}",
        f"蓝=原v6左={lv_v6_o}",
        f"绿虚=正文左={p.get('body_left')}",
        f"橙框=侧眉右={eyebrow_right(p)}",
        f"青框=cv2块(inp) 灰框=cv2块(raw)",
    ]
    pad = 8
    lh = 38
    bw = max(draw.textlength(s, font=fnt) for s in lines) + 2 * pad
    bh = len(lines) * lh + 2 * pad
    bx, by = 10, H - bh - 10
    draw.rectangle([bx, by, bx + bw, by + bh], fill=(255, 255, 255, 210))
    for i, s in enumerate(lines):
        color = (255, 0, 0) if i == 1 else (0, 0, 0)
        draw.text((bx + pad, by + pad + i * lh), s, font=fnt, fill=color)

    img.save(str(OUT / f"p{n:04d}.png"))
    print(f"  存 p{n:04d}.png  (重调v6左={lv_ret_o} 原v6左={lv_v6_o} 正文左={p.get('body_left')} 侧眉右={eyebrow_right(p)})")


for n in under:
    render(n)
# 额外渲染 D 存在页（inp 域仅 p703 / p933）查看 D 作用
extra = [n for n in (703, 933) if any(x["n"] == n for x in DB)]
for n in extra:
    render(n)
alln = list(under) + extra
print(f"\n已存 {len(alln)} 张到 {OUT}/  (含D页 {extra})")

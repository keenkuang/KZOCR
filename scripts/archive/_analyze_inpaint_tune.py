"""在 inpainted 域重调 v6 系数。
目标：约束 v6-left <= body_left（零过裁）下，最小化侧眉欠裁。
对比基准：无 inpaint 的 v6（已知 过裁=0 / 欠裁=46）。
"""
from __future__ import annotations

import itertools
import json

import numpy as np

db = json.loads(open("crop_master_inpaint.json").read())


def eyebrow_right(p):
    ey = p.get("eyebrow") or {}
    r = ey.get("right")
    return int(r) if r is not None else None


def f_left(A, B, C, D, c):
    if A is not None and B is not None:
        if A <= B:
            cand = [A - c["a1"], B - c["b1"]]
            if C is not None:
                cand.append(C + c["c1"])
            return min(cand)
        else:
            cand = [B - c["b2"]]
            if C is not None:
                cand.append(C + c["c2"])
            return min(cand)
    elif B is not None:  # A None -> noA
        cand = [B - c["b3"]]
        if C is not None:
            cand.append(C + c["c3"])
        if D is not None:
            cand.append((D + B) / 2 - c["d1"])
        return min(cand)
    elif C is not None:  # B None -> 用 C 兜底
        return C + c["c4"]
    return None


def evaluate(c):
    over = under = none = 0
    for p in db:
        A, B, C, D, _ = p["abcd_inp"]
        lv = f_left(A, B, C, D, c)
        if lv is None:
            none += 1
            continue
        lv_orig = lv + p.get("crop_left", 0)  # 裁后坐标 → 原图坐标
        bl = p.get("body_left")
        er = eyebrow_right(p)
        if bl is not None and lv_orig > bl:
            over += 1
        if er is not None and er > lv_orig:
            under += 1
    return over, under, none


# 网格（聚焦主导分支 A<=B / B<A，noA 极少固定）
grid = {
    "a1": [0, 10, 20],
    "b1": [40, 60, 80, 100],
    "c1": [0, 20, 40, 60],
    "b2": [10, 30, 50, 70],
    "c2": [0, 20, 40, 60],
    "c4": [20, 40, 60],
    "b3": [50], "c3": [60], "d1": [15],  # noA 极少，固定
}


def evaluate_on_blocks(abcd_key, c):
    """在指定 abcd 集(abcd_raw/abcd_inp)上评估公式 c，坐标已对齐原图。"""
    over = under = none = 0
    for p in db:
        A, B, C, D, _ = p[abcd_key]
        lv = f_left(A, B, C, D, c)
        if lv is None:
            none += 1
            continue
        lv_orig = lv + p.get("crop_left", 0)
        bl = p.get("body_left")
        er = eyebrow_right(p)
        if bl is not None and lv_orig > bl:
            over += 1
        if er is not None and er > lv_orig:
            under += 1
    return over, under, none


# —— 正确坐标基准 ——
V6 = {"a1": 10, "b1": 60, "c1": 60, "b2": 10, "c2": 60,
      "b3": 30, "c3": 80, "d1": 15, "c4": 60}
o1, u1, n1 = evaluate_on_blocks("abcd_raw", V6)       # 无 inpaint + 原 v6
o2, u2, n2 = evaluate_on_blocks("abcd_inp", V6)       # inpainted + 原 v6
print(f"正确坐标基准:")
print(f"  无 inpaint + 原v6:    过裁={o1} 欠裁={u1} 无B={n1}")
print(f"  inpainted + 原v6:     过裁={o2} 欠裁={u2} 无B={n2}")

keys = list(grid)
best = None
results = []
for vals in itertools.product(*[grid[k] for k in keys]):
    c = dict(zip(keys, vals))
    over, under, none = evaluate(c)
    results.append((over, under, none, c))
    if over == 0 and (best is None or under < best[1]):
        best = (over, under, none, c)

print(f"\n搜索空间: {len(results)} 组合")
print(f"\n=== 零过裁中最优(欠裁最小) ===")
if best:
    over, under, none, c = best
    print(f"过裁={over} 欠裁={under} 无B页={none}")
    print("系数:", {k: c[k] for k in keys})
    # 展示公式
    print(f"\n公式(inpainted域):")
    print(f"  A<=B: min(A-{c['a1']}, B-{c['b1']}, C+{c['c1']})")
    print(f"  B<A : min(B-{c['b2']}, C+{c['c2']})")
    print(f"  noA : min(B-{c['b3']}, C+{c['c3']}, (D+B)/2-{c['d1']})")
    print(f"  B=None: C+{c['c4']}")

# 也列出过裁=0 的 top5 欠裁
zero_over = sorted([r for r in results if r[0] == 0], key=lambda r: r[1])[:5]
print(f"\n=== 零过裁 TOP5 ===")
for over, under, none, c in zero_over:
    print(f"  欠裁={under} 无B={none} | " + " ".join(f"{k}={c[k]}" for k in keys))

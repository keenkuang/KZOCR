"""v5 问题页按公式分支统计：ABCD 取值范围 + 问题页码。"""
from __future__ import annotations

import json
from pathlib import Path

data = json.loads(
    (Path(__file__).parent / "edge15_v6_per_page.json").read_text())
prob = [r for r in data if r["oc"] or r["resid"]]


def rng(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "None"
    return f"[{min(vals):.0f}, {max(vals):.0f}] (n={len(vals)})"


for branch in ("A<=B", "B<A", "noA"):
    rows = [r for r in prob if r["branch"] == branch]
    if not rows:
        print(f"\n=== 分支 {branch}: 无问题页 ===")
        continue
    A = [r["A"] for r in rows]
    B = [r["B"] for r in rows]
    C = [r["C"] for r in rows]
    D = [r["D"] for r in rows]
    oc = [r["n"] for r in rows if r["oc"]]
    cutb = [r["n"] for r in rows if r["cut_body"]]
    resid = [r["n"] for r in rows if r["resid"]]
    print(f"\n=== 分支 {branch}: 问题页 {len(rows)} 张 ===")
    print(f"  A 范围: {rng(A)}")
    print(f"  B 范围: {rng(B)}")
    print(f"  C 范围: {rng(C)}")
    print(f"  D 范围: {rng(D)}")
    print(f"  过裁(dl)   : {len(oc)} 页  {oc}")
    print(f"  过裁(正文) : {len(cutb)} 页  {cutb}")
    print(f"  侧眉欠裁   : {len(resid)} 页  {resid}")
    # 全部问题页码
    alln = sorted(r["n"] for r in rows)
    print(f"  全部问题页 ({len(alln)}): {alln}")

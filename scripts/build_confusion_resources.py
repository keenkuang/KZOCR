#!/usr/bin/env python3
"""构建两层形近错误黑名单资源（Layer1 字符级 / Layer2 词组级）。

输入（来自用户提供的《中医学古籍OCR形近易错字符黑名单》）：
  - L1/L2/L3：三级单字表 {基准字(correct): [wrong candidates]}
  - PHRASE_RISK：方剂专有双字词风险组（词组级，M6 语义层种子）
  - 现有 kzocr/resources/confusion_set.json：历史 curated 条目（多为词组级）

输出：
  - kzocr/resources/confusion_set.json   （Layer1：单字级，含 level 字段）
  - kzocr/resources/confusion_phrase.json（Layer2：词组级）

规则：
  - 单字 correct+单字 wrong → Layer1；多字（任一） → Layer2。
  - 剔除非字（wrong==correct / category=='正确' / 别名等效不进强制层）。
  - 同一 correct 跨级别重复候选自动合并，level 取最高级（一级>二级>三级）。
  - 现有 curated 条目：单字形似 → Layer1；词组/音似/缺字 → Layer2；自环/正确 → 丢弃。
"""
from __future__ import annotations

import json
import os

RES = os.path.join(os.path.dirname(__file__), "..", "kzocr", "resources")

# ── 一级高危：方剂/炮制/针灸高频，认错直接改药效/治法 ──
L1 = {
    "炙": ["灸"], "灸": ["炙"],
    "芩": ["苓"], "苓": ["芩"],
    "萸": ["臾"], "臾": ["萸"],
    "未": ["末"], "末": ["未"],
    "己": ["已", "巳"], "已": ["己", "巳"], "巳": ["己", "已"],
    "朴": ["补"], "补": ["朴"],
    "加": ["剂"], "剂": ["加"],
    "菀": ["苑"], "苑": ["菀"],
    "抟": ["搏"], "搏": ["抟"],
    "裹": ["裏"], "裏": ["裹"],
    "蘖": ["孽"], "孽": ["蘖"],
    "附": ["咐"],
    "螵": ["嫖"], "蛸": ["梢"],
    "薤": ["韭"], "柘": ["拓"],
    "茋": ["芪"], "芪": ["茋"],
    "藁": ["槁"], "槁": ["藁"],
    "菖": ["昌"], "蒲": ["浦"],
    "蕲": ["靳"], "戟": ["棘"],
    "芍": ["勺"], "芎": ["弓"],
    "羌": ["姜"], "姜": ["羌"],
    "桂": ["柱"], "枝": ["枚"],
    "葛": ["曷"], "防": ["妨"],
    "参": ["叅"], "蓍": ["耆"], "耆": ["蓍"],
}

# ── 二级中频：本草/脉理/病证常用，刻本极易混淆 ──
L2 = {
    "燥": ["躁"], "躁": ["燥"],
    "辨": ["辩", "辫"], "辩": ["辨"],
    "癥": ["徵"], "徵": ["癥"],
    "虚": ["虗"], "虖": ["呼"],
    "痰": ["淡"], "淡": ["痰"],
    "嗽": ["漱"], "痹": ["痺"],
    "痿": ["萎"], "萎": ["痿"],
    "痈": ["雍"], "疽": ["疸"], "疸": ["疽"],
    "胀": ["涨", "帐"], "喘": ["湍"],
    "衄": ["衂"], "溺": ["弱"], "溲": ["搜"],
    "厥": ["阙"], "阙": ["厥"],
    "息": ["悉"], "脉": ["脈"], "络": ["洛"],
    "经": ["径"], "腑": ["腐"],
    "脏": ["藏"], "藏": ["脏"],
    "阴": ["隂"], "阳": ["昜"],
    "邪": ["耶"], "正": ["止"],
    "和": ["知"], "温": ["湿"], "湿": ["温"],
    "清": ["青"], "寒": ["塞"], "塞": ["寒"],
    "热": ["势"], "泄": ["泻"], "泻": ["泄"],
    "升": ["生"], "生": ["升"],
    "降": ["绛"], "敛": ["俭"],
    "散": ["撒"], "通": ["逥"], "行": ["衍"],
    "止": ["上"], "愈": ["逾"],
    "瘥": ["差"], "差": ["瘥"],
}

# ── 三级通用：干支/度量/行文高频混淆 ──
L3 = {
    "戊": ["戍", "戌"], "戍": ["戊", "戌"], "戌": ["戊", "戍"],
    "戎": ["戒"], "戒": ["戎"],
    "日": ["目", "曰"], "目": ["日"], "曰": ["日"],
    "土": ["士"], "士": ["土"],
    "大": ["太"], "太": ["大"],
    "人": ["入"], "入": ["人"],
    "千": ["干"], "干": ["千"],
    "天": ["夫"], "夫": ["天"],
    "元": ["无"], "无": ["元"],
    "云": ["去"], "去": ["云"],
    "古": ["右"], "右": ["古"], "石": ["右"],
    "寸": ["才"], "分": ["兮"],
    "两": ["丙"], "合": ["仝"],
    "及": ["乃"], "乃": ["及"],
    "即": ["既"], "既": ["即"],
    "若": ["苦"], "苦": ["若"],
    "如": ["知"], "知": ["如"],
    "有": ["存"], "存": ["有"],
    "其": ["甚"], "甚": ["其"],
    "者": ["老"], "老": ["者"],
    "诸": ["者", "储"],
    "凡": ["丸"], "丸": ["凡"],
    "方": ["彷"], "彷": ["方"],
    "汤": ["荡"], "药": ["樂"], "樂": ["药"],
    "明": ["朋"],
    "秘": ["密"], "密": ["秘"],
}

LEVEL_NAME = {"L1": "一级高危", "L2": "二级中频", "L3": "三级通用"}
PRIO = {"L1": 0, "L2": 1, "L3": 2}


def check_blacklist(black_dict):
    """自检黑名单格式（用户规范）：value 必须是列表，且不得包含自身（key）。

    捕获复制粘贴失误（如 "麻黄":["麻黄"] 自环）与语法错误（value 非列表）。
    返回 True 表示通过；False 表示存在异常（并逐条打印）。
    """
    err = []
    for k, vlist in black_dict.items():
        if not isinstance(vlist, list):
            err.append(f"{k} 值不是列表（应为 [形近错字...]）")
            continue
        for v in vlist:
            if k == v:
                err.append(f"{k}: 包含自身 {v}（自匹配无效，应删除）")
    if err:
        for e in err:
            print("黑名单异常：", e)
        return False
    return True


def _norm(cands):
    if isinstance(cands, str):
        return [cands]
    return list(cands)


def build():
    # 构建前自检：捕获自匹配/非列表值（用户规范），防止后续人工扩充引入无效条目
    for _name, _tbl in (("L1", L1), ("L2", L2), ("L3", L3)):
        if not check_blacklist(_tbl):
            raise SystemExit(f"[build] 黑名单 {_name} 自检未通过，请修正后再生成资源")

    # (correct, wrong) -> {levels:set}
    l1_groups: dict[tuple[str, str], set] = {}
    layer2_pairs: set[tuple[str, str]] = set()  # (correct, wrong)

    for lvl, table in (("L1", L1), ("L2", L2), ("L3", L3)):
        for correct, cands in table.items():
            for wrong in _norm(cands):
                if wrong == correct:
                    continue
                if len(correct) == 1 and len(wrong) == 1:
                    l1_groups.setdefault((correct, wrong), set()).add(lvl)
                else:
                    layer2_pairs.add((correct, wrong))

    # Layer1 条目
    layer1 = []
    for (correct, wrong), levels in sorted(l1_groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        top = min(levels, key=lambda lv: PRIO[lv])
        name = LEVEL_NAME[top]
        layer1.append({
            "wrong": wrong, "correct": correct,
            "category": name, "level": name,
            "note": f"{correct} 易被误识别为 {wrong}（{name}）",
        })

    # ── Layer2：用户多字条目 + 方剂双字词风险组 + 现有 curated 词组 ──
    phrase_risk = [
        ("黄芩", "黄苓"), ("吴茱萸", "吴萸臾"), ("白附片", "白付片"),
        ("白附子", "白付子"), ("海螵蛸", "海嫖蛸"), ("桑螵蛸", "桑嫖蛸"),
        ("土茯苓", "土伏苓"), ("茯苓", "伏苓"), ("半枝莲", "半支莲"),
        ("胡黄连", "湖黄连"), ("肉桂", "肉挂"),
        ("白附", "白付"), ("防己", "防巳"),
        # 桂枝→桂枝 为自环，已排除
    ]
    for correct, wrong in phrase_risk:
        if wrong != correct:
            layer2_pairs.add((correct, wrong))

    # 现有 curated（历史 confusion_set.json，已固化为常量，避免与生成的 Layer1 互相覆盖）
    OLD_CURATED = [
        {"wrong": "我术", "correct": "莪术", "category": "形似"},
        {"wrong": "黄芹", "correct": "黄芩", "category": "形似"},
        {"wrong": "半下", "correct": "半夏", "category": "音似"},
        {"wrong": "白木", "correct": "白术", "category": "形似"},
        {"wrong": "夕草", "correct": "甘草", "category": "形似"},
        {"wrong": "玉金", "correct": "郁金", "category": "音似"},
        {"wrong": "夕加", "correct": "血竭", "category": "字形误"},
        {"wrong": "姜虫", "correct": "僵虫", "category": "音似"},
        {"wrong": "生夕", "correct": "生晒", "category": "形似"},
        {"wrong": "夕傅", "correct": "血府", "category": "形似"},
        {"wrong": "王不流行", "correct": "王不留行", "category": "缺字"},
        {"wrong": "栀了", "correct": "栀子", "category": "形似"},
        {"wrong": "大吉", "correct": "大蓟", "category": "形似"},
        {"wrong": "小吉", "correct": "小蓟", "category": "形似"},
        {"wrong": "山枝", "correct": "山栀", "category": "音似"},
        {"wrong": "双白", "correct": "桑白", "category": "音似"},
        {"wrong": "双枝", "correct": "桑枝", "category": "音似"},
        {"wrong": "双叶", "correct": "桑叶", "category": "音似"},
        {"wrong": "全虫", "correct": "全蝎", "category": "别名等效"},
        {"wrong": "勾藤", "correct": "钩藤", "category": "形似"},
    ]
    for row in OLD_CURATED:
        w, c = row.get("wrong"), row.get("correct")
        cat = row.get("category", "")
        if not w or not c or w == c or cat == "正确":
            continue
        if len(w) == 1 and len(c) == 1:
            if (c, w) not in l1_groups:  # 单字形似且用户表未覆盖 → Layer1
                l1_groups.setdefault((c, w), set()).add("L2")
        else:
            layer2_pairs.add((c, w))

    # Layer2 条目
    layer2 = []
    for correct, wrong in sorted(layer2_pairs, key=lambda kv: (kv[0], kv[1])):
        layer2.append({
            "wrong": wrong, "correct": correct,
            "category": "词组/形近或语义",
            "note": f"{correct} 易被误识别为 {wrong}（词组级，M6语义校验）",
        })

    return layer1, layer2


def main():
    layer1, layer2 = build()
    p1 = os.path.join(RES, "confusion_set.json")
    p2 = os.path.join(RES, "confusion_phrase.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(layer1, f, ensure_ascii=False, indent=2)
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(layer2, f, ensure_ascii=False, indent=2)
    # 统计
    from collections import Counter
    lc = Counter(r["level"] for r in layer1)
    print(f"[ok] Layer1 字符级: {len(layer1)} 条 -> {p1}")
    for k in ("一级高危", "二级中频", "三级通用"):
        print(f"      {k}: {lc.get(k, 0)}")
    print(f"[ok] Layer2 词组级: {len(layer2)} 条 -> {p2}")


if __name__ == "__main__":
    main()

"""kzocr.scheduler.cross_align 单元测试（借鉴 ocr_pipeline_v2 验证设计）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kzocr.scheduler.cross_align import (
    align_boxes_to_text,
    align_engines,
    load_confusion_keys_split,
    run_cross_align,
    strip_punct,
    write_divergences,
)

# 形近字黑名单（wrong -> correct），取自 KZOCR resources/confusion_set.json 口径
CONFUSION = {"芩": "苓", "炙": "灸", "黄": "皇", "麥": "麦"}


def test_strip_punct_removes_punct_and_ws():
    assert strip_punct("方，木。\n水 火") == "方木水火"
    assert strip_punct("") == ""


def test_align_boxes_a_aligned_to_stripped_text():
    """boxes_a 与去标点后的 a 等长时，分歧点的 boxes 正确取自对应位置。"""
    a = "附子三钱"
    b = "附子二钱"
    boxes_a = [[0, 0, 1, 1], [1, 1, 2, 2], [2, 2, 3, 3], [3, 3, 4, 4]]
    divs = align_engines(a, b, confusion_set=CONFUSION, boxes_a=boxes_a)
    # 唯一分歧：a 的「三」(位置 2) 被替换为 b 的「二」
    assert len(divs) == 1
    assert divs[0].a_seg == "三"
    assert divs[0].boxes == [[2, 2, 3, 3]]


def test_align_boxes_a_length_mismatch_no_crash():
    """boxes_a 长度与去标点后的 a 不符时，放弃框而非 IndexError / 静默错配。"""
    a = "附子三钱"
    b = "附子二钱"
    # 长度不符（多于/少于字符数）均不得崩溃，且不应产生错位框
    for bad in ([[0, 0, 1, 1]], [[0, 0, 1, 1]] * 10):
        divs = align_engines(a, b, confusion_set=CONFUSION, boxes_a=bad)
        assert divs
        assert all(d.boxes == [] for d in divs)


def test_align_boxes_to_text_strips_punct_and_aligns():
    """逐行 char_boxes 展平后，与去标点文本逐字对齐（含首尾/中间标点）。"""
    text_a = "黄芪三钱，方用萆薢分清饮。"
    # 12 字符 → 12 框（逐字 1:1），标点也占一框
    boxes = [[[i, 0, i + 1, 1] for i in range(len(text_a))]]
    out = align_boxes_to_text(text_a, boxes)
    assert out is not None
    # 去标点后剩 11 字 → 11 框
    assert len(out) == len(strip_punct(text_a))
    assert len(out) == 11
    # 第 3 字「三」(index 2) 对应框应保留且位置正确
    assert out[2] == [2, 0, 3, 1]


def test_align_boxes_to_text_multiline():
    """多行 char_boxes 展平后整体对齐（适配器无行分隔符拼接）。"""
    line1 = "黄芪三钱"      # 4 字
    line2 = "当归二钱"      # 4 字
    text_a = line1 + line2  # 无分隔符拼接，8 字
    boxes = [
        [[i, 0, i + 1, 1] for i in range(4)],
        [[i, 1, i + 1, 2] for i in range(4)],
    ]
    out = align_boxes_to_text(text_a, boxes)
    assert out is not None
    assert len(out) == 8
    # 第二行首字「当」(index 4) 来自第二个框行
    assert out[4] == [0, 1, 1, 2]


def test_align_boxes_to_text_length_mismatch_returns_none():
    """框数 ≠ 文本字符数（如某行缺框）→ 返回 None（降级整页，不误配）。"""
    text_a = "黄芪三钱"
    # 少一框
    boxes = [[[0, 0, 1, 1], [1, 0, 2, 1], [2, 0, 3, 1]]]
    assert align_boxes_to_text(text_a, boxes) is None
    # 多一框
    boxes2 = [[[i, 0, i + 1, 1] for i in range(5)]]
    assert align_boxes_to_text(text_a, boxes2) is None


def test_align_boxes_to_text_no_boxes_returns_none():
    """无 char_boxes / 空 → 返回 None。"""
    assert align_boxes_to_text("黄芪三钱", None) is None
    assert align_boxes_to_text("黄芪三钱", []) is None
    assert align_boxes_to_text("黄芪三钱", [[]]) is None


def test_cross_align_uses_boxes_a_from_char_boxes():
    """端到端：char_boxes → align_boxes_to_text → run_cross_align 后单字分歧带 1 框。"""
    text_a = "黄芪三钱，方用萆薢分清饮"
    text_b = "黄芪二钱，方用萆薢分清饮"  # 仅「三↔二」分歧
    boxes = [[[i, 0, i + 1, 1] for i in range(len(text_a))]]
    boxes_a = align_boxes_to_text(text_a, boxes)
    assert boxes_a is not None
    divs = run_cross_align(0, text_a, text_b, confusion_set=CONFUSION, boxes_a=boxes_a)
    # 唯一分歧：单字「三」→ 1 框（box_guided 可用）
    assert len(divs) == 1
    assert divs[0].a_seg == "三"
    assert divs[0].b_seg == "二"
    assert divs[0].boxes == [[2, 0, 3, 1]]




def test_align_engines_identical_no_divergence():
    text = "治宜清热解毒利水消肿"
    divs = align_engines(text, text, confusion_set=CONFUSION)
    assert divs == []


def test_align_engines_replace_captures_segment():
    a = "瓜蒌皮"
    b = "瓜萎皮"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert len(divs) == 1
    d = divs[0]
    assert d.div_type == "replace"
    assert d.a_seg == "蒌"
    assert d.b_seg == "萎"
    assert "【蒌】" in d.a_context


def test_numeral_divergence_is_high_priority():
    # 剂量数字分歧：三↔二（中文数字，方剂书最危险）
    a = "附子三钱"
    b = "附子二钱"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert all(d.priority == "high" for d in divs)
    assert any(d.a_seg == "三" and d.b_seg == "二" for d in divs)


def test_confusion_blacklist_is_high_priority():
    # 形近字：芩↔苓
    a = "黄芩"
    b = "黄苓"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert divs[0].priority == "high"


def test_non_confusion_normal_priority():
    a = "咳嗽"
    b = "气喘"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    assert divs[0].priority == "normal"


def test_run_cross_align_fills_page_and_engine():
    divs = run_cross_align(
        24, "附子三钱", "附子二钱", confusion_set=CONFUSION,
        engine_a="kimi", engine_b="sensenova",
    )
    assert divs
    assert all(d.page_no == 24 for d in divs)
    assert all(d.engine_a == "kimi" for d in divs)
    assert all(d.engine_b == "sensenova" for d in divs)
    assert all(d.priority == "high" for d in divs)


def test_write_divergences_roundtrip(tmp_path: Path):
    db = tmp_path / "cross.db"
    divs = run_cross_align(
        24, "附子三钱", "附子二钱", confusion_set=CONFUSION,
        engine_a="kimi", engine_b="sensenova",
    )
    n = write_divergences(db, 24, divs, engine_a="kimi", engine_b="sensenova")
    assert n == len(divs)

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT page_no, div_type, a_seg, b_seg, priority, engine_a, engine_b "
        "FROM cross_divergence ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == len(divs)
    assert rows[0][0] == 24
    assert rows[0][4] == "high"
    assert rows[0][5] == "kimi"
    assert rows[0][6] == "sensenova"


def test_write_divergences_idempotent_table(tmp_path: Path):
    # 重复调用不应抛错（CREATE TABLE IF NOT EXISTS）
    db = tmp_path / "cross.db"
    divs = align_engines("附子三钱", "附子二钱", confusion_set=CONFUSION)
    write_divergences(db, 1, divs)
    write_divergences(db, 1, divs)
    conn = sqlite3.connect(str(db))
    cnt = conn.execute("SELECT COUNT(*) FROM cross_divergence").fetchone()[0]
    conn.close()
    assert cnt == len(divs) * 2


def test_real_tcm_snippet_divergence():
    # 参考 ocr_pipeline_v2 实测：午2~3时 ↔ 午？-3时
    a = "服法：午2-3时温服"
    b = "服法：午？-3时温服"
    divs = align_engines(a, b, confusion_set=CONFUSION)
    assert divs
    # 数字 2 仍在，含数字的分歧应 high
    assert any(d.priority == "high" for d in divs)


def test_load_confusion_keys_split(tmp_path: Path):
    """验证 load_confusion_keys_split 正确分离 wrong/correct 侧。"""
    rows = [
        {"wrong": "补", "correct": "朴", "level": "一级高危", "category": "通用形近"},
        {"wrong": "炙", "correct": "灸", "level": "一级高危", "category": "通用形近"},
        {"wrong": "日", "correct": "曰", "level": "三级通用", "category": "通用形近"},
    ]
    path = tmp_path / "confusion_set.json"
    import json
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    result = load_confusion_keys_split(path=path, reload=True)
    assert "wrong" in result, f"缺少 wrong 侧: {result}"
    assert "correct" in result, f"缺少 correct 侧: {result}"

    wrong = result["wrong"]
    correct = result["correct"]

    # wrong 侧：取每条混淆对的 wrong 字符
    assert wrong.get("补") == "一级高危"
    assert wrong.get("炙") == "一级高危"
    assert wrong.get("日") == "三级通用"

    # correct 侧：取每条混淆对的 correct 字符
    assert correct.get("朴") == "一级高危"
    assert correct.get("灸") == "一级高危"
    assert correct.get("曰") == "三级通用"

    # 双向字符出现在两侧（补 在 wrong 侧，也在 correct 侧作为其他条目的... 不在此例）
    # 本例无双向，验一下不存在的字符
    assert "不存在的" not in wrong


def test_load_confusion_keys_split_bidirectional(tmp_path: Path):
    """双向混淆对（补↔朴）的字符应出现在两侧。"""
    rows = [
        {"wrong": "补", "correct": "朴", "level": "一级高危", "category": "通用形近"},
        {"wrong": "朴", "correct": "补", "level": "一级高危", "category": "通用形近"},
    ]
    path = tmp_path / "bidirectional.json"
    import json
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    result = load_confusion_keys_split(path=path, reload=True)
    # "补" 同时出现在 wrong（第一条）和 correct（第二条）
    assert result["wrong"].get("补") == "一级高危"
    assert result["correct"].get("补") == "一级高危"
    # "朴" 同时出现在 wrong（第二条）和 correct（第一条）
    assert result["wrong"].get("朴") == "一级高危"
    assert result["correct"].get("朴") == "一级高危"


def test_load_confusion_keys_split_empty_no_file(tmp_path: Path, monkeypatch):
    """缺失/空文件应返回空 wrong/correct（隔离全局 learned 集，避免环境脏数据影响）。"""
    monkeypatch.setattr(
        "kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH",
        tmp_path / "learned_empty.json",
    )
    missing = tmp_path / "nonexistent.json"
    result = load_confusion_keys_split(path=missing, reload=True)
    assert result == {"wrong": {}, "correct": {}}


# ──────── add_learned_confusion ────────

def test_add_learned_confusion_first_entry(tmp_path: Path, monkeypatch):
    """首次添加，文件不存在→创建并写入。"""
    import json
    from kzocr.scheduler.cross_align import add_learned_confusion
    p = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", p)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)
    result = add_learned_confusion("芩", "苓", source="web")
    assert result is True
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["wrong"] == "芩"
    assert data[0]["correct"] == "苓"
    assert data[0]["category"] == "learned"
    assert "created_at" in data[0]


def test_add_learned_confusion_dedup(tmp_path: Path, monkeypatch):
    """相同键值对第二次添加返回 False，不重复追加。"""
    import json
    from kzocr.scheduler.cross_align import add_learned_confusion
    p = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", p)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)
    assert add_learned_confusion("芩", "苓") is True
    assert add_learned_confusion("芩", "苓") is False  # 已存在
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 1  # 未重复


def test_add_learned_confusion_invalid(tmp_path: Path, monkeypatch):
    """空值或相等值返回 False，不写文件。"""
    from kzocr.scheduler.cross_align import add_learned_confusion
    p = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", p)
    assert add_learned_confusion("", "苓") is False
    assert add_learned_confusion("芩", "") is False
    assert add_learned_confusion("一样", "一样") is False
    assert not p.is_file()  # 未创建文件


def test_add_learned_confusion_corrupt_file(tmp_path: Path, monkeypatch):
    """已存在的 JSON 损坏文件可被覆盖。"""
    import json
    from kzocr.scheduler.cross_align import add_learned_confusion
    p = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", p)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)
    p.write_text("不是一个 json", encoding="utf-8")
    result = add_learned_confusion("芩", "苓")
    assert result is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert len(data) == 1


def test_add_learned_confusion_cache_update(tmp_path: Path, monkeypatch):
    """_CONFUSION_CACHE 非 None 时同步更新。"""
    from kzocr.scheduler.cross_align import add_learned_confusion
    cache: dict[str, str] = {}
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", tmp_path / "learned_confusion.json")
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", cache)
    add_learned_confusion("芩", "苓")
    assert cache.get("芩") == "苓"

"""Module C 差异高亮 — compute_diff 算法单元测试。

覆盖引擎 vs 共识的各类字符级 diff 分类（纯插入 / 纯删除 / 替换 / 混合），
不依赖 DOM，作为前端 JS diff 的算法真源。
"""
from __future__ import annotations

from kzocr.proofread.api import DiffToken, compute_diff


def _ops(tokens: list[DiffToken]) -> list[str]:
    return [t.op for t in tokens]


def test_both_empty() -> None:
    assert compute_diff("", "") == []


def test_identical_is_equal() -> None:
    tokens = compute_diff("伤寒论", "伤寒论")
    assert _ops(tokens) == ["equal"]
    assert tokens[0].text == "伤寒论"


def test_pure_insert() -> None:
    tokens = compute_diff("伤寒", "伤寒论")
    assert _ops(tokens) == ["equal", "insert"]
    assert tokens[0].text == "伤寒"
    assert tokens[1].op == "insert" and tokens[1].text == "论"


def test_pure_delete() -> None:
    tokens = compute_diff("伤寒论", "伤寒")
    assert _ops(tokens) == ["equal", "delete"]
    assert tokens[1].op == "delete" and tokens[1].text == "论"


def test_single_char_replace() -> None:
    tokens = compute_diff("abc", "axc")
    assert _ops(tokens) == ["equal", "replace", "equal"]
    rep = tokens[1]
    assert rep.op == "replace"
    assert rep.old == "b"
    assert rep.new == "x"
    assert rep.text == "x"


def test_word_replace() -> None:
    tokens = compute_diff("the cat sat", "the dog sat")
    ops = _ops(tokens)
    assert "replace" in ops
    # 合并为一次 replace：cat -> dog
    rep = next(t for t in tokens if t.op == "replace")
    assert rep.old == "cat"
    assert rep.new == "dog"


def test_mixed_replace_and_insert() -> None:
    # abcde -> xdeZ：整段 abc→x 合并为一次 replace，末尾插 Z
    tokens = compute_diff("abcde", "xdeZ")
    ops = _ops(tokens)
    assert "replace" in ops
    assert "insert" in ops
    rep = next(t for t in tokens if t.op == "replace")
    assert rep.old == "abc" and rep.new == "x"
    # 重建：old 侧还原 a，new 侧还原 b
    restored_a = "".join(t.old for t in tokens if t.op in ("equal", "delete", "replace"))
    restored_b = "".join(t.new for t in tokens if t.op in ("equal", "insert", "replace"))
    assert restored_a == "abcde"
    assert restored_b == "xdeZ"


def test_standalone_delete_between_equals() -> None:
    # a X b -> a b：被删的 X 两侧均为相同文本，应为独立 delete（非 replace）
    tokens = compute_diff("a X b", "a b")
    ops = _ops(tokens)
    assert "delete" in ops
    assert "replace" not in ops
    restored_a = "".join(t.old for t in tokens if t.op in ("equal", "delete", "replace"))
    restored_b = "".join(t.new for t in tokens if t.op in ("equal", "insert", "replace"))
    assert restored_a == "a X b"
    assert restored_b == "a b"


def test_empty_target_all_delete() -> None:
    tokens = compute_diff("伤寒论", "")
    assert all(t.op == "delete" for t in tokens)
    assert "".join(t.text for t in tokens) == "伤寒论"


def test_empty_source_all_insert() -> None:
    tokens = compute_diff("", "伤寒论")
    assert all(t.op == "insert" for t in tokens)
    assert "".join(t.text for t in tokens) == "伤寒论"


def test_whitespace_and_cjk() -> None:
    a = "太阳病 ， 脉浮"
    b = "太阳病，脉浮数"
    tokens = compute_diff(a, b)
    # 删除标点空格、插入“数”
    assert "delete" in _ops(tokens)
    assert "insert" in _ops(tokens)
    restored_a = "".join(t.old for t in tokens if t.op in ("equal", "delete", "replace"))
    restored_b = "".join(t.new for t in tokens if t.op in ("equal", "insert", "replace"))
    assert restored_a == a
    assert restored_b == b

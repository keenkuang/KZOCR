"""kzocr/scheduler/cross_align.py 纯逻辑补充单测（零网络，仅文件与黑名单自检逻辑）。

现有 test_cross_align.py 已覆盖对齐主路径与 load_confusion_keys_split；此处补
_load_confusion_file 边界（缺失/损坏/非 list/跳过无效行）、validate_confusion_rows 告警、
load_confusion_keys 空文件分支。
"""

from __future__ import annotations

import json
from pathlib import Path

from kzocr.scheduler.cross_align import (
    _load_confusion_file,
    load_confusion_keys,
    validate_confusion_rows,
)


def test_load_confusion_file_missing() -> None:
    p = Path("/nonexistent/confusion.json")
    assert _load_confusion_file(p) == {}
    assert _load_confusion_file(p, raw=True) == []


def test_load_confusion_file_corrupt(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text("{bad", encoding="utf-8")
    assert _load_confusion_file(f) == {}
    assert _load_confusion_file(f, raw=True) == []


def test_load_confusion_file_not_a_list(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text('{"wrong":"x"}', encoding="utf-8")
    assert _load_confusion_file(f) == {}


def test_load_confusion_file_skips_invalid_rows(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text(
        json.dumps(
            [
                {"wrong": "麻", "correct": "蔴", "category": "正确"},  # category 正确 → 跳过
                {"wrong": "麻", "correct": "麻"},                     # 自匹配 → 跳过
                {"wrong": "黄", "correct": "簧"},                     # 保留
                {"bad": "x"},                                         # 非 dict → 跳过
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert _load_confusion_file(f) == {"黄": "簧"}


def test_validate_confusion_rows_warns(capsys) -> None:
    validate_confusion_rows(
        [
            "not dict",
            {"wrong": "a"},
            {"wrong": "x", "correct": "x"},
        ]
    )
    out = capsys.readouterr().out
    assert "不是对象" in out
    assert "缺少 wrong/correct" in out
    assert "自身" in out


def test_load_confusion_keys_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH",
        tmp_path / "learned_empty.json",
    )
    assert load_confusion_keys(tmp_path / "missing.json", reload=True) == {}

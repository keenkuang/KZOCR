"""kzocr/engine/registration.py 纯逻辑单测（零网络/零引擎，仅文件 I/O）。

覆盖 save/load 往返、max_depth 计算、缺失/损坏文件回退、列表过滤、registration_to_toc 转换与空值分支。
"""

from __future__ import annotations

from kzocr.engine.registration import (
    list_registrations,
    load_registration,
    registration_to_toc,
    save_registration,
)


def test_save_roundtrip_computes_max_depth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path))
    toc = [
        {"level": 1, "title": "上", "page": 1},
        {"level": 2, "title": "中", "page": 2},
        {"level": 3, "title": "下", "page": 3},
    ]
    data = save_registration("bk", title="书", toc_entries=toc)
    assert data["toc"]["max_depth"] == 3
    assert data["toc"]["entries"] == toc
    loaded = load_registration("bk")
    assert loaded["title"] == "书"
    assert loaded["toc"]["max_depth"] == 3


def test_load_missing_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path))
    assert load_registration("nope") is None


def test_load_corrupt_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path))
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")
    assert load_registration("bad") is None


def test_list_missing_dir_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path / "none"))
    assert list_registrations() == []


def test_list_filters_broken_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path))
    save_registration("a")
    # 损坏文件：list 应跳过而非崩溃
    (tmp_path / "broken.json").write_text("{xx", encoding="utf-8")
    regs = list_registrations()
    assert [r["book_code"] for r in regs] == ["a"]


def test_registration_to_toc_none_cases() -> None:
    assert registration_to_toc({}) is None
    assert registration_to_toc({"toc": {"entries": []}}) is None


def test_registration_to_toc_nested(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_DATA_DIR", str(tmp_path))
    reg = {
        "toc": {
            "max_depth": 2,
            "entries": [
                {
                    "level": 1, "title": "卷一", "page": 1, "section_no": "1",
                    "sub_entries": [
                        {"level": 2, "title": "节", "page": 2, "section_no": "1.1"}
                    ],
                }
            ],
        }
    }
    tree = registration_to_toc(reg)
    assert tree is not None
    assert tree.max_depth == 2
    assert tree.entries[0].title == "卷一"
    assert tree.entries[0].sub_entries[0].title == "节"
    assert tree.entries[0].sub_entries[0].section_no == "1.1"

"""kzocr/resources/__init__.py 补充纯逻辑单测（零外部资源 / temp 目录）。

与原 test_resources.py（TestResourceStore 覆盖加载成功路径 + 叠加层各分支）互补，
仅补其未覆盖的缺口（原覆盖率 79%）：
- 种子资源文件缺失时的降级（51-53）
- ``_empty_for`` 空值分支（141-143）
- 叠加层标量覆盖（100）：现有 dict/list 合并已覆盖，标量覆盖分支未覆盖

注意：``load()`` 内部引用模块级全局 ``_RESOURCE_FILES``，故种子缺失测试通过
monkeypatch 全局来驱动 FileNotFoundError 分支。
"""
from __future__ import annotations

from kzocr import resources
from kzocr.resources import ResourceStore, _empty_for


def test_empty_for_variant_map() -> None:
    assert _empty_for("variant_map") == {}


def test_empty_for_other() -> None:
    assert _empty_for("confusion_set") == []
    assert _empty_for("anything") == []


def test_module_load_entry(monkeypatch) -> None:
    # 模块级 load() 入口（130）：调用不致异常，单例已加载则早返回
    resources.load()
    assert isinstance(resources.get(), ResourceStore)


def test_seed_files_missing_falls_back_to_empty(monkeypatch) -> None:
    # 将资源文件名指向不存在的文件 → 触发 FileNotFoundError 分支（51-53）
    monkeypatch.setattr(resources, "_RESOURCE_FILES", {
        "variant_map": "zz_missing_variant.json",
        "confusion_set": "zz_missing_confusion.json",
        "rare_allowlist": "zz_missing_rare.json",
        "toxic_herbs": "zz_missing_toxic.json",
    })
    store = ResourceStore()
    store.load()
    assert store.variant_map() == {}
    assert store.confusion_set() == []
    assert store.rare_allowlist() == []
    assert store.toxic_herbs() == []


def _fresh_store_with_overlay_dir(tmp_path, monkeypatch):
    """构造已加载、叠加层基目录（模块全局 _RESOURCE_DIR）为 tmp_path 的空 store。"""
    monkeypatch.setattr(resources, "_RESOURCE_DIR", tmp_path)
    store = ResourceStore()
    store._data = {
        "variant_map": {},
        "confusion_set": [],
        "rare_allowlist": [],
        "toxic_herbs": [],
    }
    store._loaded = True
    return store


def test_overlay_missing_file(tmp_path, monkeypatch) -> None:
    store = _fresh_store_with_overlay_dir(tmp_path, monkeypatch)
    # 路径在受控目录内但文件不存在 → 78-79
    store._load_overlay(str(tmp_path / "nope.json"))
    assert store.variant_map() == {}


def test_overlay_invalid_json(tmp_path, monkeypatch) -> None:
    store = _fresh_store_with_overlay_dir(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    # 文件存在但 JSON 解析失败 → 84-86
    store._load_overlay(str(bad))
    assert store.variant_map() == {}


def test_overlay_not_a_dict(tmp_path, monkeypatch) -> None:
    store = _fresh_store_with_overlay_dir(tmp_path, monkeypatch)
    not_dict = tmp_path / "list.json"
    not_dict.write_text("[1, 2, 3]", encoding="utf-8")
    # 顶层非 dict → 89-90
    store._load_overlay(str(not_dict))
    assert store.variant_map() == {}


def test_overlay_dict_merge(tmp_path, monkeypatch) -> None:
    store = _fresh_store_with_overlay_dir(tmp_path, monkeypatch)
    overlay = tmp_path / "merge.json"
    overlay.write_text('{"variant_map": {"麤": "粗"}}', encoding="utf-8")
    # 现有 dict + 叠加 dict → update（96）
    store._load_overlay(str(overlay))
    assert store.variant_map()["麤"] == "粗"


def test_overlay_scalar_override(tmp_path, monkeypatch) -> None:
    store = _fresh_store_with_overlay_dir(tmp_path, monkeypatch)
    overlay = tmp_path / "scalar.json"
    overlay.write_text('{"variant_map": "not-a-dict"}', encoding="utf-8")
    # 现有 dict 但叠加值非 dict/list → 标量覆盖（100）
    store._load_overlay(str(overlay))
    assert store._data["variant_map"] == "not-a-dict"


"""B5: 种子资源加载测试。"""
from __future__ import annotations

import os
import json
from pathlib import Path


from kzocr.resources import ResourceStore, get


class TestResourceStore:
    def test_load_all_files(self):
        """验证 4 个种子文件全部成功加载。"""
        store = ResourceStore()
        store.load()
        assert len(store.variant_map()) > 0
        assert len(store.confusion_set()) > 0
        assert len(store.rare_allowlist()) > 0
        assert len(store.toxic_herbs()) > 0

    def test_variant_map_content(self):
        """验证 variant_map 包含关键繁简映射。"""
        store = ResourceStore()
        store.load()
        vm = store.variant_map()
        assert vm["黃"] == "黄"
        assert vm["參"] == "参"
        assert "異" in vm

    def test_confusion_set_structure(self):
        """验证 confusion_set 条目包含必需字段。"""
        store = ResourceStore()
        store.load()
        for entry in store.confusion_set():
            assert "correct" in entry
            assert "wrong" in entry
            assert "category" in entry
            assert "note" in entry

    def test_rare_allowlist_structure(self):
        """验证 rare_allowlist 条目包含必需字段。"""
        store = ResourceStore()
        store.load()
        for entry in store.rare_allowlist():
            assert "term" in entry
            assert "pinyin" in entry
            assert "category" in entry
            assert "description" in entry

    def test_toxic_herbs_structure(self):
        """验证 toxic_herbs 条目包含必需字段。"""
        store = ResourceStore()
        store.load()
        for entry in store.toxic_herbs():
            assert "herb" in entry
            assert "max_dosage_g" in entry
            assert "usual_dosage_g" in entry
            assert "toxic_component" in entry
            assert "note" in entry
            assert isinstance(entry["max_dosage_g"], (int, float))

    def test_toxic_herbs_max_dosage_positive(self):
        """验证所有毒药的极量 > 0。"""
        store = ResourceStore()
        store.load()
        for entry in store.toxic_herbs():
            assert entry["max_dosage_g"] > 0, f'{entry["herb"]} max_dosage must > 0'

    def test_get_singleton(self):
        """验证 get() 返回单例。"""
        s1 = get()
        s2 = get()
        assert s1 is s2

    def test_load_idempotent(self):
        """验证 load() 幂等。"""
        store = ResourceStore()
        store.load()
        vm1 = store.variant_map()
        store.load()  # 第二次调用不应重置状态
        vm2 = store.variant_map()
        assert vm1 == vm2

    def test_overlay_respects_path_security(self):
        """验证叠加层路径安全校验：不在 resources 目录下则忽略。"""
        store = ResourceStore()
        # 模拟不安全路径
        old = os.environ.get("KZOCR_TERM_KB_PATH")
        os.environ["KZOCR_TERM_KB_PATH"] = "/tmp/malicious.json"

        # 创建临时文件
        Path("/tmp/malicious.json").write_text(
            json.dumps({"variant_map": {"惡": "恶"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        store.load()
        km = store.variant_map()
        assert "惡" not in km, "不安全的叠加层应被拒绝"

        # 清理
        Path("/tmp/malicious.json").unlink(missing_ok=True)
        if old is None:
            del os.environ["KZOCR_TERM_KB_PATH"]
        else:
            os.environ["KZOCR_TERM_KB_PATH"] = old

    def test_overlay_merges(self):
        """验证合法的叠加层可合入。"""
        resources_dir = Path(__file__).resolve().parent.parent / "kzocr" / "resources"
        overlay_path = resources_dir / "_test_overlay.json"
        old = os.environ.get("KZOCR_TERM_KB_PATH")

        try:
            overlay_path.write_text(
                json.dumps({
                    "confusion_set": [
                        {
                            "wrong": "测试错字",
                            "correct": "测试正字",
                            "category": "测试",
                            "note": "测试叠加层条目",
                        }
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            os.environ["KZOCR_TERM_KB_PATH"] = str(overlay_path)

            store = ResourceStore()
            store.load()
            conf = store.confusion_set()
            assert any(c["wrong"] == "测试错字" for c in conf)

        finally:
            overlay_path.unlink(missing_ok=True)
            if old is None:
                del os.environ["KZOCR_TERM_KB_PATH"]
            else:
                os.environ["KZOCR_TERM_KB_PATH"] = old

    def test_overlay_missing_path(self):
        """验证不存在的叠加层路径被忽略不抛异常。"""
        old = os.environ.get("KZOCR_TERM_KB_PATH")
        os.environ["KZOCR_TERM_KB_PATH"] = "/nonexistent/path.json"

        store = ResourceStore()
        store.load()  # 不应抛异常

        if old is None:
            del os.environ["KZOCR_TERM_KB_PATH"]
        else:
            os.environ["KZOCR_TERM_KB_PATH"] = old

    def test_json_validity(self):
        """验证 4 个 JSON 文件语法正确。"""
        resources_dir = Path(__file__).resolve().parent.parent / "kzocr" / "resources"
        for filename in [
            "variant_map.json",
            "confusion_set.json",
            "rare_allowlist.json",
            "toxic_herbs.json",
        ]:
            path = resources_dir / filename
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data is not None

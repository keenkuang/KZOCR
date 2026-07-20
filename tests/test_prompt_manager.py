"""kzocr/engine/prompt_manager.py 纯逻辑单测（覆盖率补测，原 74%）。

覆盖 save/load 往返、缺失返回 None、损坏文件回退、list 含加载失败项、
delete、init_defaults 幂等、KZOCR_PROMPT_DIR 环境覆盖。
"""
from __future__ import annotations

import kzocr.engine.prompt_manager as pm


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    pm.save_prompt("foo", "中医OCR")
    assert pm.load_prompt("foo") == "中医OCR"


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    assert pm.load_prompt("nope") is None


def test_load_corrupt_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
    assert pm.load_prompt("bad") is None


def test_list_prompts_includes_failure_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    pm.save_prompt("ok", "好")
    (tmp_path / "broken.json").write_text("}{", encoding="utf-8")
    listing = pm.list_prompts()
    names = {item["name"] for item in listing}
    assert "ok" in names
    assert "broken" in names
    broken = [i for i in listing if i["name"] == "broken"][0]
    assert broken["text"] == "(加载失败)"


def test_delete_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    pm.save_prompt("del", "x")
    assert pm.load_prompt("del") == "x"
    pm.delete_prompt("del")
    assert pm.load_prompt("del") is None


def test_init_defaults_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    pm.init_defaults()
    assert pm.load_prompt("check_prompt") == pm.DEFAULT_CHECK_PROMPT
    assert pm.load_prompt("correct_prompt") == pm.DEFAULT_CORRECT_PROMPT
    # 二次调用不覆盖已有模板，验证不抛错且值不变
    pm.init_defaults()
    assert pm.load_prompt("check_prompt") == pm.DEFAULT_CHECK_PROMPT


def test_prompt_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(tmp_path))
    assert pm._prompt_dir() == str(tmp_path)

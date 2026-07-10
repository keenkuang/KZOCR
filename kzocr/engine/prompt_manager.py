"""Prompt 管理器：存储和管理质检 prompt 模板。"""

from __future__ import annotations

import json
import os
from typing import Optional

_PROMPT_DIR_ENV = "KZOCR_PROMPT_DIR"


def _prompt_dir() -> str:
    d = os.environ.get(_PROMPT_DIR_ENV, os.path.join(os.getcwd(), "prompts"))
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str) -> str:
    return os.path.join(_prompt_dir(), f"{name}.json")


DEFAULT_CHECK_PROMPT = """请审核下方中医方剂的解析结果。方剂编号：{recipe_no}，标题：{title}

字段内容：
{fields}

药材列表：
{herbs}

规则检查发现以下疑点：
{issues}

请逐条回答：1) 此疑点是否真实问题？2) 如需修正请给出建议。
如无问题请回答「全部正确」。"""


DEFAULT_CORRECT_PROMPT = """请修正以下方剂的解析错误：

{errors}

原始文本：{raw_text}

请输出修正后的完整方剂信息。"""


def save_prompt(name: str, text: str) -> None:
    """保存 prompt 模板。"""
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump({"name": name, "text": text}, f, ensure_ascii=False, indent=2)


def load_prompt(name: str) -> Optional[str]:
    """加载 prompt 模板。"""
    p = _path(name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)["text"]
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def list_prompts() -> list[dict]:
    """列出所有 prompt 模板。"""
    d = _prompt_dir()
    if not os.path.isdir(d):
        return []
    prompts = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                    prompts.append({"name": data.get("name", f[:-5]), "text": data.get("text", "")[:80]})
            except Exception:
                prompts.append({"name": f[:-5], "text": "(加载失败)"})
    return prompts


def delete_prompt(name: str) -> None:
    """删除 prompt 模板。"""
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)


def init_defaults() -> None:
    """初始化默认 prompt 模板（仅缺失时创建）。"""
    for name, text in [("check_prompt", DEFAULT_CHECK_PROMPT), ("correct_prompt", DEFAULT_CORRECT_PROMPT)]:
        if load_prompt(name) is None:
            save_prompt(name, text)

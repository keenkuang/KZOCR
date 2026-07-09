"""适配器公共工具 — AdapterPageResult → LineResult 统一转换（B2 裁决）。

v0.3 FREEZE B2 裁决：
- 任何适配器都不得自行折算 LineResult，否则退回 run.py 单体化。
- adapter_to_line_result() 是唯一的转换入口，在 EngineRouter 汇集结果时调用。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from kzocr.engine.types import (
    AdapterMeta,
    AdapterPageResult,
    EngineResult,
    LineResult,
)

logger = logging.getLogger(__name__)


def adapter_to_line_result(
    apr: AdapterPageResult,
    engine_name: str,
    page_idx: int = 0,
    para_seq: int = 1,
    line_seq: int = 1,
) -> LineResult:
    """页级适配器结果 → 行级 LineResult。

    这是 B2 裁决指定的唯一转换函数：
    1. text → final + engine_texts[engine_name]
    2. confidence → LineResult.confidence
    3. char_confidences → JSON 序列化进 char_level_json
    4. crop_img_path → 透传
    5. engine_name → 额外填入 engine_texts 用于共识比对

    Args:
        apr: 适配器返回的页级结果。
        engine_name: 引擎内部名称（如 "paddleocr"）。
        page_idx: 页码（仅日志用）。
        para_seq: 段落序号（默认 1）。
        line_seq: 行序号（默认 1）。

    Returns:
        折算后的 LineResult 实例。
    """
    # 处理 char_confidences → char_level_json
    char_level_json: Optional[str] = None
    if apr.char_confidences is not None:
        try:
            if len(apr.char_confidences) != len(apr.text):
                logger.warning(
                    "[adapter_to_line] %s P%d: char_confidences 长度 %d 与 text 长度 %d 不一致，截断",
                    engine_name, page_idx + 1,
                    len(apr.char_confidences), len(apr.text),
                )
                n = min(len(apr.char_confidences), len(apr.text))
                truncated = apr.char_confidences[:n]
            else:
                truncated = apr.char_confidences
            char_level_json = json.dumps({"conf": truncated}, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[adapter_to_line] %s P%d: char_confidences JSON 序列化失败: %s",
                engine_name, page_idx + 1, exc,
            )

    # 构建 engine_texts
    engine_texts: dict[str, str] = {}
    if apr.text:
        engine_texts[engine_name] = apr.text

    return LineResult(
        sequence_in_paragraph=line_seq,
        engine_texts=engine_texts,
        consensus=apr.text,
        final=apr.text,
        confidence=apr.confidence,
        char_level_json=char_level_json,
        crop_img_path=apr.crop_img_path,
        engine_results=[EngineResult(
            engine=engine_name,
            text=apr.text,
            confidence=apr.confidence,
        )],
    )

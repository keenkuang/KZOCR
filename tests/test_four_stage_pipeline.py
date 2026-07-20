"""W3 tcm_ocr 两处 TODO 收口：锁定 four_stage_pipeline 现有行为。

冻结栈原则：不引入新依赖、不改主线行为，仅用回归测试锁定已闭环的
方剂聚合逻辑（heading 纯文本启发式 + 方剂名 heading 回填）。
tcm_ocr 为平行冻结栈，缺依赖时本文件整体 skip，不影响主线 CI。
"""
from __future__ import annotations

import pytest

fs = pytest.importorskip("kzocr.tcm_ocr.llm.pipeline.four_stage_pipeline")


def _unit(text: str) -> list:
    return [{"consensus_text": text}]


# ───────────── _backfill_formula_name（方剂名 heading 回填，W3 :200 闭环） ─────────────


def test_backfill_fills_when_empty():
    block = {"formula_name": None}
    fs._backfill_formula_name(block, "卷一 总论")
    assert block["formula_name"] == "卷一 总论"


def test_backfill_skips_when_present():
    block = {"formula_name": "已有方"}
    fs._backfill_formula_name(block, "卷一")
    assert block["formula_name"] == "已有方"


# ───────────── _classify_para_unit（heading 纯文本启发式，W3 :143 deferred 标注） ─────────────


def test_classify_heading_short():
    # 短 + 无方剂标记 + 不像药材续行 → heading
    assert fs._classify_para_unit(_unit("卷一")) == fs.BLOCK_ROLE_HEADING


def test_classify_text_long():
    long = "夫医道之源本乎阴阳，脏腑经络气血津液，皆有其常度，失常则为病。"
    assert fs._classify_para_unit(_unit(long)) == fs.BLOCK_ROLE_TEXT

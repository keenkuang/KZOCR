"""W3: tcm_ocr 方剂聚合层 formula_name 收口测试。

覆盖：
- _extract_formula_name 接入 utils.common.extract_formula_name（优先匹配方剂尾词）
- _extract_formula_name 在 common 无匹配时回退到「组成/方药/处方/方剂」标记前文本
- group_into_formula_blocks 对前序 heading 块文本回填 formula_name
- formula 块自身能提取到名时不覆盖
"""

from __future__ import annotations

from kzocr.tcm_ocr.llm.pipeline.four_stage_pipeline import (
    BLOCK_ROLE_FORMULA,
    BLOCK_ROLE_HEADING,
    _extract_formula_name,
    group_into_formula_blocks,
)


def _unit(consensus_text: str, page_number: int = 1, para_index: int = 0) -> dict:
    return {
        "consensus_text": consensus_text,
        "page_number": page_number,
        "para_index": para_index,
    }


def test_extract_formula_name_via_common():
    # common.extract_formula_name 优先匹配以方剂尾词结尾的 2-8 字词
    # （前一个方剂词后的标点会切断匹配，故取干净的「方名+组成」结构）
    unit = [_unit("六味地黄丸组成：熟地山药山萸肉")]
    assert _extract_formula_name(unit) == "六味地黄丸"


def test_extract_formula_name_marker_fallback():
    # 无方剂尾词但含「组成」标记时，回退取标记前文本
    unit = [_unit("本方组成如下")]
    assert _extract_formula_name(unit) == "本方"


def test_extract_formula_name_none():
    assert _extract_formula_name([_unit("")]) is None
    assert _extract_formula_name([_unit("水煎服，日三服")]) is None


def test_group_backfills_heading():
    # heading 块在前、formula 块在后：formula_name 回填前序 heading 文本
    groups = [
        [_unit("四君子汤")],
        [_unit("组成：人参白术茯苓甘草各等分")],
    ]
    blocks = group_into_formula_blocks(groups)
    roles = [b["role"] for b in blocks]
    assert roles == [BLOCK_ROLE_HEADING, BLOCK_ROLE_FORMULA]
    assert blocks[1]["formula_name"] == "四君子汤"


def test_group_keeps_own_name_over_heading():
    # formula 块自身能提取名时不被前序 heading 覆盖
    groups = [
        [_unit("古方")],
        [_unit("半夏泻心汤组成：半夏黄芩黄连干姜")],
    ]
    blocks = group_into_formula_blocks(groups)
    formula_block = next(b for b in blocks if b["role"] == BLOCK_ROLE_FORMULA)
    assert formula_block["formula_name"] == "半夏泻心汤"


def test_group_independent_formula_backfills_heading():
    # 加减方（CONT_INDEPENDENT）独立成块，自身无名时回填前序 heading
    groups = [
        [_unit("四君子汤")],
        [_unit("组成：人参白术")],
        # 「原方加茯苓三钱」含剂量使其不被判 heading，detect_reference_type
        # 触发 add_to_above → CONT_INDEPENDENT 独立成块
        [_unit("原方加茯苓三钱")],
    ]
    blocks = group_into_formula_blocks(groups)
    formula_blocks = [b for b in blocks if b["role"] == BLOCK_ROLE_FORMULA]
    assert len(formula_blocks) == 2
    # 第二块为加减方，自身无方剂尾词、无「组成」标记 → 回填前序 heading
    assert formula_blocks[1]["formula_name"] == "四君子汤"

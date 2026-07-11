"""
LLM Prompt 模板构建模块。

为中医 OCR 校对系统提供完整的 Prompt 构建能力：
- 本地 LLM（ShizhenGPT）校对 Prompt
- 云端 LLM 仲裁 Prompt
- 跨页段落处理提示
- 方剂上下文提示
- 自动检测并注入上下文标记

所有 Prompt 均遵循"忠于原文、不得脑补"的核心原则。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────
# Prompt 模板常量
# ───────────────────────────────────────────────────────

LOCAL_LLM_PROMPT_TEMPLATE: str = \
"""你是一位中医出版物 OCR 校对专家。你的任务是校对 OCR 识别结果，纠正识别错误，但必须忠于原文，不得脑补或添加原文不存在的内容。

## 输入格式
1. 段落图像（你将看到原始扫描图像）
2. 行级 OCR 结果（每行包含文本和字符级 bbox）
3. 共识文本（多引擎投票结果）

## 校对原则（按优先级排序）
1. **忠于原文**：只做 OCR 纠错，不做内容润色；不添加原文没有的文字
2. **行数守恒**：输入多少行，输出必须多少行，不得合并或拆分
3. **字符级校对**：关注易混淆字（术↔木、芩↔苓、己↔已、炙↔灸等）
4. **中医术语**：药材名、穴位名、方剂名需符合《中国药典》和《腧穴名称与定位》标准
5. **剂量敏感**：数字 + 单位（g/克/钱/两）不得随意修改
6. **否定词敏感**：不/无/非/忌/禁/勿/慎 等否定词修改需特别谨慎
7. **有毒药材**：附子、朱砂、雄黄、马钱子等有毒药材名称修改需高置信度

## 输出格式（严格 JSON）
```json
{
  "corrected_lines": ["校对后的第1行", "校对后的第2行", ...],
  "changes": [
    {
      "line_index": 0,
      "char_index": 5,
      "original": "木",
      "corrected": "术",
      "reason": "上下文为中药材，应为'白术'而非'白木'",
      "confidence": 0.95
    }
  ],
  "explanation": "简要说明主要修改点"
}
```

## 约束
- 行数守恒：输入 {input_line_count} 行，输出必须 {input_line_count} 行
- 只修改明显 OCR 错误，不确定的保持原文
- changes 数组中只包含有实际修改的位置
- confidence 范围 0.0 ~ 1.0

{context_hint}

{cross_page_hint}

## 待校对文本

### 共识文本
```
{consensus_text}
```

### 引擎结果
{engine_results_text}

请严格按照上述 JSON 格式输出，不要输出任何其他内容。
"""

CLOUD_LLM_PROMPT_TEMPLATE: str = \
"""你是一位中医出版物 OCR 校对的终审专家。你将对本地 LLM 的校对结果进行复核和仲裁。

## 背景
本地 LLM 已对 OCR 结果进行了初步校对，字形验证层发现了一些疑似问题。
你需要进行最终仲裁。

## 校对原则
1. **忠于原文**：只做 OCR 纠错，不做内容润色
2. **行数守恒**：输入多少行，输出必须多少行
3. **灾难性字段**：否定词/有毒药材/剂量/穴位的修改需极高置信度
4. **字形验证失败位置**：请特别关注字形验证标记的位置

## 本地 LLM 输出
```json
{local_output_json}
```

## 字形验证失败位置
{verification_failures_text}

## 原始共识文本
```
{consensus_text}
```

## 引擎结果
{engine_results_text}

## 输出格式（严格 JSON）
```json
{
  "corrected_lines": ["最终校对的第1行", "最终校对的第2行", ...],
  "changes": [
    {
      "line_index": 0,
      "char_index": 5,
      "original": "木",
      "corrected": "术",
      "reason": "仲裁理由",
      "confidence": 0.98,
      "arbitration": "override_local" | "confirm_local" | "revert_to_original"
    }
  ],
  "explanation": "仲裁说明",
  "needs_human_review": false
}
```

## 约束
- 行数守恒：输入 {input_line_count} 行，输出必须 {input_line_count} 行
- needs_human_review: 当置信度 < 0.6 或有灾难性字段争议时设为 true
- arbitration 字段说明你的仲裁决策：
  - override_local: 推翻本地 LLM 决定
  - confirm_local: 确认本地 LLM 决定
  - revert_to_original: 恢复为原始 OCR 文本

请严格按照上述 JSON 格式输出，不要输出任何其他内容。
"""

CROSS_PAGE_HINT: str = \
"""## ⚠️ 跨页段落提示
本段落跨越多个页面（页码：{page_numbers}）。OCR 引擎按页独立识别，可能导致：
1. 页尾/页首行被错误拆断
2. 跨页行号不连续
3. 页面边缘字符被截断或变形

**处理要求**：
- 将跨页段落视为一个整体进行校对
- 检查页边界处的行是否被错误拆分/合并
- 确保跨页后语义连贯
- 页边界处字符变形需额外关注

跨页段落组 ID: {group_id}
"""

CONTEXT_HINT_FORMULA: str = \
"""## 📋 上下文提示
前一段落为**方剂**内容。当前段落可能包含：
- 方剂的组成药物列表
- 各药物的剂量
- 煎服方法
- 加减变化

**特殊关注**：
- 药物剂量数字 + 单位（g/克/钱/两）必须准确
- 有毒药物的配伍禁忌
- "先煎"、"后下"、"包煎"等特殊煎法标记
- "忌"、"禁"、"勿"等用药禁忌词

方剂名称: {formula_name}
"""

CONTEXT_HINT_PREVIOUS_PARAGRAPH: str = \
"""## 📋 上下文摘要
前一段落摘要：{prev_summary}

**连贯性要求**：
- 当前段落应与前文在语义上连贯
- 关注跨段落术语一致性
- 人名、书名、方剂名应保持一致
"""

# 块级 role 分支指令（按聚合块的 role 选择，注入 context_hint 占位）
BLOCK_ROLE_FORMULA: str = "formula"
BLOCK_ROLE_HEADING: str = "heading"
BLOCK_ROLE_TEXT: str = "text"
BLOCK_ROLE_ADD_SUB: str = "add_sub"  # 加减方（formula 且带 referenced_id）

BLOCK_ROLE_INSTRUCTION: Dict[str, str] = {
    BLOCK_ROLE_FORMULA: \
    """## 📋 块角色：方剂块
本块为**方剂**内容（可能包含方名、组成、剂量、主治、用法、加减等）。请按以下要求校对：
- **行数守恒**：输入多少行输出多少行，绝不合并/拆分/增删行。
- **剂量敏感**：数字 + 单位（g/克/钱/两/片/枚）不得改动；"先煎/后下/包煎/烊化"等特殊煎法标记保留。
- **药名准确**：药材名符合《中国药典》，注意易混字（术↔木、芩↔苓、己↔已、炙↔灸、朴↔扑）。
- **有毒药谨慎**：附子、朱砂、雄黄、马钱子、生半夏等改动需高置信度。
- **结构**：仍按行返回 corrected_lines（每行对应原文一行），可用 changes 说明关键修正；
  若原文含方名标题行，请原样保留该行。""",
    BLOCK_ROLE_ADD_SUB: \
    """## 📋 块角色：加减方块
本块为**在基础方上的加减方**（所引用基础方 block_id={referenced_id}）。请：
- **行数守恒**，绝不改动基础方的共有药味与剂量；
- 仅在"加/去/减/易"处校对本块新增或删减的药材与剂量；
- 不要补全基础方完整组成，只校对本块所见文字；
- 有毒药、剂量依旧谨慎。""",
    BLOCK_ROLE_HEADING: \
    """## 📋 块角色：标题块
本块为**单行标题**（章/节/方剂名等）。请：
- 保持单行输出，**行数守恒（必须仍为 1 行）**；
- 只做 OCR 纠错，忠于原文，不要展开成结构、不要编造或补全内容；
- 标题中的序号、标点、书名号保持原样。""",
    BLOCK_ROLE_TEXT: \
    """## 📋 块角色：正文块
本块为常规**正文**段落，按通用段落校对规则即可：行数守恒、忠于原文、易混字与中医术语准确。""",
}

# ───────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────

def _safe_format(template: str, **fields: Any) -> str:
    """按字面替换 {field} 占位符。

    Prompt 模板内含 JSON 示例（含大量花括号），若用 str.format 会把
    JSON 的 {} 当成字段解析导致 KeyError。这里用纯字符串替换，避免
    转义整段 JSON，也更安全。
    """
    out = template
    for key, value in fields.items():
        out = out.replace("{" + key + "}", str(value))
    return out

def _detect_cross_page(para_lines: List[Dict[str, Any]]) -> Tuple[bool, List[int], Optional[str]]:
    """检测是否为跨页段落。

    通过检查行记录中的 page_number 字段判断是否跨页。

    Args:
        para_lines: 段落行记录列表，每项包含 page_number 等字段。

    Returns:
        (是否跨页, 页码列表, 跨页组ID)
    """
    page_numbers: List[int] = []
    for line in para_lines:
        pn = line.get("page_number")
        if pn is not None and pn not in page_numbers:
            page_numbers.append(pn)

    is_cross_page = len(page_numbers) > 1
    group_id = para_lines[0].get("cross_page_group_id") if para_lines else None

    return is_cross_page, page_numbers, group_id


def _detect_formula_context(para_lines: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """检测前一段落是否为方剂上下文。

    通过检查段落标记或前文摘要判断。

    Args:
        para_lines: 段落行记录列表。

    Returns:
        (是否为方剂上下文, 方剂名称)
    """
    if not para_lines:
        return False, ""

    first_line = para_lines[0]

    # 检查段落类型标记
    para_type = first_line.get("previous_paragraph_type", "")
    if para_type == "formula":
        formula_name = first_line.get("formula_name", "未知方剂")
        return True, formula_name

    # 检查前文摘要中的方剂关键词
    prev_summary = first_line.get("previous_summary", "")
    formula_keywords = ["方剂", "处方", "组成", "煎服", "主治", "功效"]
    if any(kw in prev_summary for kw in formula_keywords):
        # 尝试提取方剂名
        match = re.search(r'「([^」]+)」|"([^"]+)"|《([^》]+)》', prev_summary)
        formula_name = match.group(1) or match.group(2) or match.group(3) if match else "未知方剂"
        return True, formula_name

    return False, ""


def _detect_previous_summary(para_lines: List[Dict[str, Any]]) -> Optional[str]:
    """获取前一段落的摘要。

    Args:
        para_lines: 段落行记录列表。

    Returns:
        前一段落摘要，无则返回 None。
    """
    if not para_lines:
        return None
    summary = para_lines[0].get("previous_summary", "")
    return summary if summary else None


def build_local_proofread_prompt(
    para_lines: List[Dict[str, Any]],
    consensus_text: str,
    engine_results: List[Dict[str, Any]],
) -> str:
    """构建本地 LLM 校对 Prompt。

    自动检测跨页并注入 CROSS_PAGE_HINT，
    检测前一段落为方剂时注入 CONTEXT_HINT_FORMULA。

    Args:
        para_lines: 段落行记录列表，每项包含 text, bbox, chars 等。
        consensus_text: 多引擎投票共识文本（多行字符串）。
        engine_results: 各 OCR 引擎原始结果列表。

    Returns:
        完整的本地 LLM Prompt 字符串。
    """
    # 构建引擎结果文本
    engine_results_text_parts: List[str] = []
    for idx, result in enumerate(engine_results):
        engine_name = result.get("engine", f"引擎{idx + 1}")
        engine_text = result.get("text", "")
        confidence = result.get("confidence", 0.0)
        engine_results_text_parts.append(
            f"### {engine_name} (置信度: {confidence:.2f})\n```\n{engine_text}\n```"
        )
    engine_results_text = "\n\n".join(engine_results_text_parts)

    # 计算行数
    input_line_count = len(consensus_text.split("\n")) if consensus_text else 0

    # 检测跨页
    is_cross_page, page_numbers, group_id = _detect_cross_page(para_lines)
    cross_page_hint = ""
    if is_cross_page:
        cross_page_hint = CROSS_PAGE_HINT.format(
            page_numbers=", ".join(str(p) for p in sorted(page_numbers)),
            group_id=group_id or "unknown",
        )

    # 检测方剂上下文
    is_formula, formula_name = _detect_formula_context(para_lines)
    context_hint = ""
    if is_formula:
        context_hint = CONTEXT_HINT_FORMULA.format(formula_name=formula_name)
    else:
        # 检查是否有前文摘要
        prev_summary = _detect_previous_summary(para_lines)
        if prev_summary:
            context_hint = CONTEXT_HINT_PREVIOUS_PARAGRAPH.format(
                prev_summary=prev_summary
            )

    # 构建行级 OCR 结果文本
    ocr_lines_text = "\n".join(
        f"行 {i}: {line.get('text', '')}"
        for i, line in enumerate(para_lines)
    )

    # 组装 Prompt
    prompt = _safe_format(
        LOCAL_LLM_PROMPT_TEMPLATE,
        input_line_count=input_line_count,
        context_hint=context_hint or "## 上下文提示\n无特殊上下文。",
        cross_page_hint=cross_page_hint or "",
        consensus_text=consensus_text,
        engine_results_text=engine_results_text,
        ocr_lines_text=ocr_lines_text,
    )

    logger.debug("[PromptBuilder] 本地 Prompt 构建完成 | 行数=%d | 跨页=%s | 方剂=%s",
                 input_line_count, is_cross_page, is_formula)

    return prompt


def build_block_prompt(
    role: str,
    para_lines: List[Dict[str, Any]],
    consensus_text: str,
    engine_results: List[Dict[str, Any]],
    formula_name: Optional[str] = None,
    referenced_id: Optional[str] = None,
) -> str:
    """构建块级（按 role）校对 Prompt。

    与 build_local_proofread_prompt 同构，但 context_hint 不再依赖
    _detect_formula_context（读上一行 previous_paragraph_type，对聚合块不适用），
    改为按块 role 选择指令（见 BLOCK_ROLE_INSTRUCTION）。

    Args:
        role: "formula" | "heading" | "text"
        para_lines / consensus_text / engine_results: 同 build_local_proofread_prompt
        formula_name: 方剂名（formula 块可选，注入提示）

    Returns:
        完整 Prompt 字符串。
    """
    engine_results_text_parts: List[str] = []
    for idx, result in enumerate(engine_results):
        engine_name = result.get("engine", f"引擎{idx + 1}")
        engine_text = result.get("text", "")
        confidence = result.get("confidence", 0.0)
        engine_results_text_parts.append(
            f"### {engine_name} (置信度: {confidence:.2f})\n```\n{engine_text}\n```"
        )
    engine_results_text = "\n\n".join(engine_results_text_parts)

    input_line_count = len(consensus_text.split("\n")) if consensus_text else 0

    is_cross_page, page_numbers, group_id = _detect_cross_page(para_lines)
    cross_page_hint = ""
    if is_cross_page:
        cross_page_hint = CROSS_PAGE_HINT.format(
            page_numbers=", ".join(str(p) for p in sorted(page_numbers)),
            group_id=group_id or "unknown",
        )

    # 加减方（formula 且带 referenced_id）用专属指令
    if role == BLOCK_ROLE_FORMULA and referenced_id:
        role_instruction = BLOCK_ROLE_INSTRUCTION[BLOCK_ROLE_ADD_SUB].format(
            referenced_id=referenced_id
        )
    else:
        role_instruction = BLOCK_ROLE_INSTRUCTION.get(
            role, BLOCK_ROLE_INSTRUCTION[BLOCK_ROLE_TEXT]
        )
    if role == BLOCK_ROLE_FORMULA and formula_name:
        role_instruction += f"\n方剂名称: {formula_name}"
    context_hint = (
        f"## 上下文提示\n{role_instruction}" if role_instruction
        else "## 上下文提示\n无特殊上下文。"
    )

    ocr_lines_text = "\n".join(
        f"行 {i}: {line.get('text', '')}" for i, line in enumerate(para_lines)
    )

    prompt = _safe_format(
        LOCAL_LLM_PROMPT_TEMPLATE,
        input_line_count=input_line_count,
        context_hint=context_hint,
        cross_page_hint=cross_page_hint or "",
        consensus_text=consensus_text,
        engine_results_text=engine_results_text,
        ocr_lines_text=ocr_lines_text,
    )
    logger.debug(
        "[PromptBuilder] 块级 Prompt 构建完成 | role=%s | 行数=%d",
        role, input_line_count,
    )
    return prompt


def build_cloud_proofread_prompt(
    para_lines: List[Dict[str, Any]],
    consensus_text: str,
    engine_results: List[Dict[str, Any]],
    local_output: Dict[str, Any],
    local_verification: Dict[str, Any],
) -> str:
    """构建云端 LLM 仲裁 Prompt。

    包含字形验证失败位置详情，供云端模型进行最终仲裁。

    Args:
        para_lines: 段落行记录列表。
        consensus_text: 多引擎投票共识文本。
        engine_results: 各 OCR 引擎原始结果列表。
        local_output: 本地 LLM 输出结果。
        local_verification: 本地字形验证结果。

    Returns:
        完整的云端 LLM Prompt 字符串。
    """
    # 构建引擎结果文本
    engine_results_text_parts: List[str] = []
    for idx, result in enumerate(engine_results):
        engine_name = result.get("engine", f"引擎{idx + 1}")
        engine_text = result.get("text", "")
        confidence = result.get("confidence", 0.0)
        engine_results_text_parts.append(
            f"### {engine_name} (置信度: {confidence:.2f})\n```\n{engine_text}\n```"
        )
    engine_results_text = "\n\n".join(engine_results_text_parts)

    # 计算行数
    input_line_count = len(consensus_text.split("\n")) if consensus_text else 0

    # 构建本地输出 JSON
    import json
    try:
        local_output_json = json.dumps(local_output, ensure_ascii=False, indent=2)
    except Exception:
        local_output_json = str(local_output)

    # 构建字形验证失败文本
    verification_failures = local_verification.get("failed_lines", [])
    critical_intercepts = local_verification.get("critical_intercept", [])

    failures_text_parts: List[str] = []

    if critical_intercepts:
        failures_text_parts.append("### 🚨 灾难性拦截（需特别关注）")
        for ci in critical_intercepts:
            failures_text_parts.append(
                f"- 行 {ci['line_index']}, 位置 {ci['char_index']}: "
                f"'{ci['original']}' → '{ci['llm_suggested']}' | "
                f"{ci['reason']} (置信度: {ci['confidence']:.2f})"
            )

    if verification_failures:
        failures_text_parts.append("\n### ⚠️ 字形验证失败")
        for vf in verification_failures:
            for failure in vf.get("failures", []):
                if failure.get("decision") == "keep_original":
                    failures_text_parts.append(
                        f"- 行 {vf['line_index']}, 位置 {failure['char_index']}: "
                        f"'{failure['original']}' → '{failure['llm_suggested']}' | "
                        f"保留原文 (置信度: {failure.get('confidence', 0):.2f})"
                    )

    if not failures_text_parts:
        verification_failures_text = "字形验证全部通过，无需特别关注。"
    else:
        verification_failures_text = "\n".join(failures_text_parts)

    # 组装 Prompt
    prompt = _safe_format(
        CLOUD_LLM_PROMPT_TEMPLATE,
        input_line_count=input_line_count,
        local_output_json=local_output_json,
        verification_failures_text=verification_failures_text,
        consensus_text=consensus_text,
        engine_results_text=engine_results_text,
    )

    logger.debug("[PromptBuilder] 云端 Prompt 构建完成 | 失败行=%d | 拦截=%d",
                 len(verification_failures), len(critical_intercepts))

    return prompt


def build_cross_page_prompt_segment(
    page_number: int,
    lines_in_page: List[str],
    is_first_page: bool = False,
    is_last_page: bool = False,
) -> str:
    """构建跨页段落的单页提示片段。

    用于在本地 LLM Prompt 中标记各页内容。

    Args:
        page_number: 页码。
        lines_in_page: 该页包含的行文本列表。
        is_first_page: 是否为首页。
        is_last_page: 是否为末页。

    Returns:
        该页的提示文本。
    """
    boundary_info = ""
    if is_first_page:
        boundary_info = " [首页 - 关注页尾是否被截断]"
    elif is_last_page:
        boundary_info = " [末页 - 关注页首是否与前页连续]"
    else:
        boundary_info = " [中间页 - 关注页首页尾连续性]"

    lines_text = "\n".join(f"  {i}: {line}" for i, line in enumerate(lines_in_page))

    return f"### 第 {page_number} 页{boundary_info}\n{lines_text}"


def build_batch_proofread_prompt(
    paragraph_groups: List[Dict[str, Any]],
) -> str:
    """构建批量校对 Prompt（用于跨页段落组）。

    将多个跨页段落作为一个整体进行校对。

    Args:
        paragraph_groups: 段落组列表，每项包含 para_lines, consensus_text, engine_results。

    Returns:
        批量校对的 Prompt 字符串。
    """
    sections: List[str] = []

    for idx, group in enumerate(paragraph_groups):
        para_lines = group.get("para_lines", [])
        consensus_text = group.get("consensus_text", "")
        page_num = group.get("page_number", idx + 1)

        section = f"""
## 段落 {idx + 1}（第 {page_num} 页）
```
{consensus_text}
```
"""
        sections.append(section)

    full_text = "\n".join(sections)

    prompt = f"""你正在校对一段跨越 {len(paragraph_groups)} 个页面的连续段落。
这些段落本是一个整体，但被分页拆开了。请将它们视为一个整体进行校对。

{full_text}

## 校对要求
1. 将上述所有段落视为一个整体语义单元
2. 检查跨页边界处的行是否被错误拆分
3. 确保术语在不同页面间保持一致
4. 输出每个段落校对后的文本，保持段落分隔

请按段落顺序输出 JSON 格式：
```json
{{
  "paragraphs": [
    {{
      "paragraph_index": 0,
      "corrected_lines": ["行1", "行2", ...],
      "changes": [...]
    }}
  ]
}}
```
"""
    return prompt


def extract_json_from_response(response_text: str) -> Dict[str, Any]:
    """从 LLM 响应中提取 JSON。

    支持多种格式：
    1. 纯 JSON 字符串
    2. Markdown 代码块中的 JSON
    3. 文本中嵌入的 JSON

    Args:
        response_text: LLM 响应文本。

    Returns:
        解析后的字典。

    Raises:
        ValueError: 无法解析 JSON。
    """
    import json

    text = response_text.strip()

    # 尝试 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 ```json ... ``` 代码块
    code_block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试 3: 查找第一个 JSON 对象
    json_match = re.search(r"(\{[\s\S]*\})", text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: 返回原始文本包裹
    logger.warning("[PromptBuilder] 无法从响应中提取 JSON，返回原始文本")
    return {"raw_text": text, "parse_error": True}
